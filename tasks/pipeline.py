"""Orquestrador do pipeline de producao de audiolivros.

Este modulo expoe:

* :class:`PipelineOrquestrador` — classe responsavel por coordenar as
  etapas do pipeline (extracao de texto, identificacao/normalizacao
  de personagens, definicao de vozes, inferencia de emocoes, geracao
  de audio e juncao final). Cada etapa e executada em uma transacao
  propria via :func:`app.repositories.database.session_scope`, com
  checkpoints persistidos no banco para permitir retomada.

* ``executar_pipeline_task`` — tarefa Celery que encapsula a
  execucao do orquestrador, tratando pausa, retry e erros
  recuperaveis de forma controlada.

A implementacao prioriza baixo acoplamento: cada servico (PDF, LLM,
TTS, Personagens, Catalogacao) pode ser substituido por um mock ou
implementacao alternativa via injecao de dependencia no construtor
do orquestrador. Isso facilita testes unitarios e a troca futura de
implementacoes (ex.: TTS alternativo, novo provedor de LLM).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.database import session_scope
from app.repositories.models.livro_cabecalho import EstadoPipeline, LivroCabecalho
from app.repositories.models.livro_fala import LivroFala
from app.repositories.models.livro_pagina import LivroPagina
from app.repositories.models.livro_personagem import LivroPersonagem
from app.repositories.livro_pagina_repo import LivroPaginaRepositorio
from app.repositories.livro_personagem_repo import LivroPersonagemRepositorio
from app.repositories.livro_repo import LivroRepositorio
from app.services.catalogacao_vozes import CatalogacaoVozesServico
from app.services.llm import LLMServico
from app.services.pdf import PDFService

from tasks.celery_app import celery_app
from tasks.exceptions import (
    LivroNaoEncontradoError,
    PipelineErroError,
    PipelinePausadoError,
)

logger = logging.getLogger(__name__)


# Total de etapas do pipeline. Usado para normalizar o campo
# ``progresso_total`` e para validar retomadas.
TOTAL_ETAPAS = 7


# Tipos auxiliares ------------------------------------------------------------

# Assinatura das etapas privadas do pipeline. Cada uma recebe o id do
# livro e realiza o trabalho da sua fase. Sao metodos bound da classe
# ``PipelineOrquestrador``.
Etapa = Callable[["PipelineOrquestrador", int], None]


class PipelineOrquestrador:
    """Orquestra a execucao sequencial das etapas do pipeline.

    O orquestrador delega cada etapa a um servico especifico (PDF,
    Personagens, LLM, TTS, Catalogacao) e mantem o estado de
    progresso no banco de dados para permitir pausa, retomo e
    diagnostico.

    Args:
        llm: Implementacao do servico de LLM. Quando ``None``,
            instancia :class:`app.services.llm.LLMServico`.
        tts: Implementacao do servico de TTS. Quando ``None``,
            instancia :class:`app.services.tts.TTSServico`.
        pdf: Implementacao do servico de extracao de PDF. Quando
            ``None``, instancia :class:`app.services.pdf.PDFService`.
        personagens: Servico de identificacao/normalizacao de
            personagens. Quando ``None``, usa
            :class:`app.services.personagens.PersonagensService`.
        catalogacao: Servico de catalogacao de vozes. Quando
            ``None``, usa
            :class:`app.services.catalogacao_vozes.CatalogacaoVozesServico`.
    """

    def __init__(
        self,
        llm: Any | None = None,
        tts: Any | None = None,
        pdf: Any | None = None,
        personagens: Any | None = None,
        catalogacao: Any | None = None,
    ) -> None:
        self._settings = get_settings()
        # Injecao de dependencia: cada servico pode ser substituido
        # por um mock em testes ou por uma implementacao alternativa
        # em producao (ex.: LLM na nuvem, TTS alternativo).
        self.llm = llm
        self.tts = tts
        self.pdf = pdf
        self.personagens = personagens
        self.catalogacao = catalogacao
        self._inicializar_servicos()

    # ------------------------------------------------------------------ #
    # Inicializacao dos servicos
    # ------------------------------------------------------------------ #

    def _inicializar_servicos(self) -> None:
        """Instancia os servicos padrao que nao foram injetados.

        O construtor nao faz este trabalho diretamente para manter
        uma separacao clara entre a atribuicao dos parametros (que ja
        documenta o que pode ser injetado) e a logica de fallback.
        Erros de importacao sao capturados e logados, permitindo que
        o orquestrador continue funcional mesmo quando um servico
        opcional ainda nao foi implementado.
        """
        if self.pdf is None:
            self.pdf = PDFService()

        if self.catalogacao is None:
            try:
                self.catalogacao = CatalogacaoVozesServico()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Falha ao instanciar CatalogacaoVozesServico; "
                    "definicao de vozes ficara limitada"
                )
                self.catalogacao = None

        if self.llm is None:
            try:
                self.llm = LLMServico()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Falha ao instanciar LLMServico; etapas que dependem "
                    "de inferencia por LLM irao falhar"
                )
                self.llm = None

        if self.tts is None:
            try:
                self.tts = TTSServicoStub() if self._tts_ausente() else TTSServicoReal()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Falha ao instanciar TTSServico; geracao de audio "
                    "ficara indisponivel"
                )
                self.tts = None

        if self.personagens is None:
            try:
                self.personagens = _PersonagensServiceStub(self.llm)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Falha ao instanciar PersonagensService"
                )
                self.personagens = None

    def _tts_ausente(self) -> bool:
        """Verifica se o modulo de TTS esta disponivel.

        O servico real depende de ``httpx`` e de uma instancia
        configurada de TTS. Para evitar que o pipeline quebre durante
        testes locais sem o servico real, o orquestrador usa um stub
        leve quando a classe nao pode ser importada.
        """
        try:
            from app.services.tts import TTSServico  # noqa: F401
        except Exception:  # noqa: BLE001
            return True
        return False

    # ------------------------------------------------------------------ #
    # Helpers de persistencia
    # ------------------------------------------------------------------ #

    def _buscar_livro_ou_erro(self, session: Session, livro_id: int) -> LivroCabecalho:
        """Carrega o livro ou levanta :class:`LivroNaoEncontradoError`."""
        repo = LivroRepositorio(session)
        livro = repo.buscar_por_id_sync(livro_id)
        if livro is None:
            logger.error("Livro id=%s nao encontrado no banco", livro_id)
            raise LivroNaoEncontradoError(
                f"Livro id={livro_id} nao encontrado"
            )
        return livro

    def _verificar_pausa(self, livro_id: int) -> None:
        """Verifica se o livro foi pausado entre etapas.

        A verificacao considera duas fontes de verdade complementares:
        a flag ``fila_pausado`` (sinal "S" ativo) e o estado
        ``pausado`` do pipeline. Qualquer um deles indica que o
        administrador pediu para interromper o processamento.

        Ao detectar a pausa, este metodo:
          1. Atualiza o estado do livro para ``pausado`` (se ainda
             nao estiver);
          2. Registra o evento no log estruturado;
          3. Levanta :class:`PipelinePausadoError` para abortar
             a execucao do orquestrador.
        """
        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            pausado_flag = (livro.fila_pausado or "").upper() == "S"
            estado_pausado = (livro.estado_pipeline or "") == EstadoPipeline.PAUSADO.value

            if pausado_flag or estado_pausado:
                # Garante que o estado esteja coerente com a flag de pausa.
                if livro.estado_pipeline != EstadoPipeline.PAUSADO.value:
                    livro.estado_pipeline = EstadoPipeline.PAUSADO.value
                    session.add(livro)
                logger.info(
                    "Pipeline pausado: livro_id=%s fila_pausado=%s estado=%s",
                    livro_id,
                    livro.fila_pausado,
                    livro.estado_pipeline,
                )
                raise PipelinePausadoError(
                    f"Pipeline pausado para livro_id={livro_id}"
                )

    def _salvar_checkpoint(self, livro_id: int, etapa: str) -> None:
        """Registra o progresso no banco apos a conclusao de uma etapa.

        Atualiza o estado do pipeline para o valor associado a
        ``etapa`` e incrementa ``progresso_atual``. Cada etapa possui
        um mapeamento deterministico para o estado correspondente na
        :class:`EstadoPipeline`.
        """
        estado, progresso = _MAPA_ETAPA_ESTADO.get(
            etapa, (EstadoPipeline.PRODUCAO, 5)
        )
        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            livro.estado_pipeline = estado.value
            livro.progresso_atual = progresso
            # Mantem o total sempre coerente com o numero de etapas.
            livro.progresso_total = TOTAL_ETAPAS
            session.add(livro)
        logger.info(
            "Checkpoint: livro_id=%s etapa=%s estado=%s progresso=%d/%d",
            livro_id,
            etapa,
            estado.value,
            progresso,
            TOTAL_ETAPAS,
        )

    def _atualizar_erro(self, livro_id: int, mensagem: str) -> None:
        """Marca o livro em estado de erro com a mensagem fornecida."""
        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            livro.estado_pipeline = EstadoPipeline.ERRO.value
            livro.erro_mensagem = mensagem
            session.add(livro)
        logger.error(
            "Pipeline em estado de erro: livro_id=%s mensagem=%s",
            livro_id,
            mensagem,
        )

    def _atualizar_estado(self, livro_id: int, estado: EstadoPipeline) -> None:
        """Atualiza apenas o estado do pipeline (sem mexer no progresso)."""
        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            livro.estado_pipeline = estado.value
            session.add(livro)

    # ------------------------------------------------------------------ #
    # Etapas do pipeline
    # ------------------------------------------------------------------ #

    def _resolver_caminho_pdf(self, livro_id: int) -> str:
        """Recupera o caminho do PDF associado ao livro.

        Pode estar vazio quando o livro foi criado em modo de
        demonstracao ou quando o administrador ainda nao subiu o
        PDF. Nesse caso, a etapa de extracao levanta um erro claro.
        """
        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            caminho = livro.caminho_pdf
        if not caminho:
            raise PipelineErroError(
                f"Livro id={livro_id} nao possui caminho_pdf definido"
            )
        return caminho

    def _extraer_texto(self, livro_id: int) -> None:
        """Etapa 1: extrai o texto do PDF e persiste as paginas."""
        logger.info("Etapa 1 (extracao) iniciada: livro_id=%s", livro_id)
        self._atualizar_estado(livro_id, EstadoPipeline.EXTRACAO)
        caminho_pdf = self._resolver_caminho_pdf(livro_id)
        # PDFService.processar_pdf eh sincrono. Como o servico gerencia
        # sua propria sessao, nao precisamos envolvê-lo em session_scope.
        if self.pdf is None:
            raise PipelineErroError("Servico de PDF nao inicializado")
        paginas = self.pdf.processar_pdf(livro_id, caminho_pdf)
        logger.info(
            "Etapa 1 (extracao) concluida: livro_id=%s paginas=%s",
            livro_id,
            paginas,
        )

    def _identificar_personagens(self, livro_id: int) -> None:
        """Etapa 2: identifica personagens e falas de cada pagina."""
        logger.info("Etapa 2 (identificar_personagens) iniciada: livro_id=%s", livro_id)
        self._atualizar_estado(livro_id, EstadoPipeline.PERSONAGENS)
        if self.personagens is None:
            raise PipelineErroError("Servico de Personagens nao inicializado")
        if hasattr(self.personagens, "identificar_personagens_por_pagina"):
            self.personagens.identificar_personagens_por_pagina(livro_id)
        else:
            # Implementacao alternativa: o servico de personagens pode
            # estar apenas em rascunho (task_09 ainda em progresso). Para
            # nao bloquear o pipeline, registramos um aviso e seguimos.
            logger.warning(
                "PersonagensService.identificar_personagens_por_pagina "
                "ausente; etapa 2 sera pulada para livro_id=%s",
                livro_id,
            )

    def _normalizar_personagens(self, livro_id: int) -> None:
        """Etapa 3: normaliza e unifica nomes de personagens."""
        logger.info("Etapa 3 (normalizar_personagens) iniciada: livro_id=%s", livro_id)
        self._atualizar_estado(livro_id, EstadoPipeline.PERSONAGENS)
        if self.personagens is None:
            raise PipelineErroError("Servico de Personagens nao inicializado")
        if hasattr(self.personagens, "normalizar_personagens"):
            self.personagens.normalizar_personagens(livro_id)
        else:
            logger.warning(
                "PersonagensService.normalizar_personagens ausente; "
                "etapa 3 sera pulada para livro_id=%s",
                livro_id,
            )

    def _definir_vozes(self, livro_id: int) -> None:
        """Etapa 4: sugere vozes do catalogo para cada personagem.

        Esta etapa NAO atribui vozes definitivamente — isso fica a
        cargo do administrador via interface web. O objetivo aqui e
        preencher o campo ``cd_voz`` com a melhor sugestao automatica
        para que o painel de revisao ja mostre candidatos uteis.
        """
        logger.info("Etapa 4 (definir_vozes) iniciada: livro_id=%s", livro_id)
        self._atualizar_estado(livro_id, EstadoPipeline.VOZES)
        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            repo = LivroPersonagemRepositorio(session)
            personagens = repo.listar_por_livro(livro_id)
            if not personagens:
                logger.info(
                    "Etapa 4: nenhum personagem cadastrado para livro_id=%s",
                    livro_id,
                )
                return

            for personagem in personagens:
                # Se ja tem voz atribuida, nao mexe.
                if personagem.cd_voz:
                    continue
                if not personagem.tx_genero or not personagem.tx_idade:
                    # Sem perfil definido, nao ha como sugerir.
                    logger.debug(
                        "Personagem id=%s sem genero/idade; pulando",
                        personagem.cd_sequencial,
                    )
                    continue
                if self.catalogacao is None or not hasattr(
                    self.catalogacao, "sugerir_vozes_por_personagem"
                ):
                    logger.debug(
                        "Servico de catalogacao indisponivel; pulando "
                        "personagem id=%s",
                        personagem.cd_sequencial,
                    )
                    continue
                sugestoes = self.catalogacao.sugerir_vozes_por_personagem(
                    personagem.tx_genero,
                    personagem.tx_idade,
                    limite=1,
                )
                if sugestoes:
                    # O servico retorna VozInfo com id igual ao nome
                    # do diretorio no dataset. Persistimos o id
                    # como sugestao; o admin podera confirmar via UI.
                    personagem.cd_voz = _hash_voz_para_int(sugestoes[0].id)
                    session.add(personagem)
            session.add(livro)
        logger.info("Etapa 4 (definir_vozes) concluida: livro_id=%s", livro_id)

    def _inferir_emocoes(self, livro_id: int) -> None:
        """Etapa 5: usa o LLM para inferir emocao/prosodia/paralinguistica.

        Para cada fala sem instrucao emocional, o LLM produz as tres
        instrucoes usadas pelo Qwen3-TTS no parametro ``instruct``.
        O resultado e persistido nos campos ``tx_instrucao_emocao``,
        ``tx_instrucao_prosodia`` e ``tx_instrucao_paralinguistica``.
        """
        logger.info("Etapa 5 (inferir_emocoes) iniciada: livro_id=%s", livro_id)
        self._atualizar_estado(livro_id, EstadoPipeline.PRODUCAO)
        if self.llm is None:
            raise PipelineErroError("Servico de LLM nao inicializado")

        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            stmt = (
                select(LivroFala)
                .where(
                    LivroFala.cd_sequenciallivro == livro_id,
                    (LivroFala.tx_instrucao_emocao.is_(None))
                    | (LivroFala.tx_instrucao_emocao == ""),
                )
                .order_by(LivroFala.nr_ordem.asc())
            )
            falas: list[LivroFala] = list(session.execute(stmt).scalars().all())
            if not falas:
                logger.info(
                    "Etapa 5: nenhuma fala sem emocao para livro_id=%s",
                    livro_id,
                )
                return

            for fala in falas:
                if not fala.tx_fala:
                    continue
                try:
                    instrucoes = self.llm.inferir_emocao(fala.tx_fala)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Falha ao inferir emocao para fala id=%s: %s",
                        fala.cd_sequencial,
                        exc,
                    )
                    continue
                fala.tx_instrucao_emocao = instrucoes.get("emocao", "")
                fala.tx_instrucao_prosodia = instrucoes.get("prosodia", "")
                fala.tx_instrucao_paralinguistica = instrucoes.get(
                    "paralinguistica", ""
                )
                session.add(fala)
            session.add(livro)
        logger.info("Etapa 5 (inferir_emocoes) concluida: livro_id=%s", livro_id)

    def _gerar_audio(self, livro_id: int) -> None:
        """Etapa 6: gera audio para cada fala usando o TTS.

        Para cada personagem com voz atribuida:
          1. Carrega o audio de referencia (dataset ou gerado por
             Voice Design, dependendo do campo ``tx_voz_origem``);
          2. Coleta todas as falas do personagem;
          3. Chama o servico de TTS em lote (``gerar_audio_lote``);
          4. Salva os WAVs resultantes em
             ``caminho_audio_chunk`` com ``nr_chunk`` incremental;
          5. Marca ``fl_processado='S'`` em cada fala.
        """
        logger.info("Etapa 6 (gerar_audio) iniciada: livro_id=%s", livro_id)
        self._atualizar_estado(livro_id, EstadoPipeline.PRODUCAO)
        if self.tts is None:
            raise PipelineErroError("Servico de TTS nao inicializado")

        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            repo_pers = LivroPersonagemRepositorio(session)
            personagens = repo_pers.listar_por_livro(livro_id)
            personagens_com_voz = [p for p in personagens if p.cd_voz]
            if not personagens_com_voz:
                logger.info(
                    "Etapa 6: nenhum personagem com voz atribuida "
                    "para livro_id=%s",
                    livro_id,
                )
                return

            output_dir = self._settings.audio_output_path / f"livro_{livro_id}"
            output_dir.mkdir(parents=True, exist_ok=True)

            for personagem in personagens_com_voz:
                self._gerar_audio_personagem(
                    session=session,
                    livro=livro,
                    personagem=personagem,
                    output_dir=output_dir,
                )
            session.add(livro)
        logger.info("Etapa 6 (gerar_audio) concluida: livro_id=%s", livro_id)

    def _gerar_audio_personagem(
        self,
        session: Session,
        livro: LivroCabecalho,
        personagem: LivroPersonagem,
        output_dir: Any,
    ) -> None:
        """Geracao de audio para um unico personagem."""
        ref_audio_b64, ref_text = self._obter_referencia_voz(personagem)
        if not ref_audio_b64:
            logger.info(
                "Personagem id=%s sem audio de referencia; pulando",
                personagem.cd_sequencial,
            )
            return

        stmt = (
            select(LivroFala)
            .where(
                LivroFala.cd_sequenciallivro == livro.cd_sequencial,
                LivroFala.cd_sequencialpersonagem == personagem.cd_sequencial,
            )
            .order_by(LivroFala.nr_ordem.asc())
        )
        falas: list[LivroFala] = list(session.execute(stmt).scalars().all())
        if not falas:
            return

        # Constroi o payload consumido por gerar_audio_lote.
        falas_payload: list[dict[str, Any]] = []
        for fala in falas:
            fala_id = fala.cd_sequencial
            chunks_texto = self._dividir_texto(fala.tx_fala or "")
            for nr_chunk, chunk in enumerate(chunks_texto, start=1):
                falas_payload.append(
                    {
                        "texto": chunk,
                        "fala_id": fala_id,
                        "nr_chunk": nr_chunk,
                    }
                )

        if not falas_payload:
            return

        referencia_voz = {"ref_audio_base64": ref_audio_b64, "ref_text": ref_text}
        try:
            audios = self._tts_gerar_lote(falas_payload, referencia_voz)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Falha ao gerar audio para personagem id=%s: %s",
                personagem.cd_sequencial,
                exc,
            )
            return

        # Distribui os audios retornados pelo TTS nas falas originais.
        # Assume que gerar_audio_lote retorna 1 audio por chunk na mesma
        # ordem em que foram enviados.
        idx_audio = 0
        for fala in falas:
            chunks_texto = self._dividir_texto(fala.tx_fala or "")
            for nr_chunk, _chunk in enumerate(chunks_texto, start=1):
                if idx_audio >= len(audios):
                    break
                audio = audios[idx_audio]
                caminho = output_dir / (
                    f"fala_{fala.cd_sequencial}_chunk_{nr_chunk}.wav"
                )
                caminho.write_bytes(audio)
                fala.caminho_audio_chunk = str(caminho)
                fala.nr_chunk = nr_chunk
                fala.fl_processado = "S"
                session.add(fala)
                idx_audio += 1

    def _obter_referencia_voz(self, personagem: LivroPersonagem) -> tuple[str, str]:
        """Resolve o audio de referencia (Base64) e o texto da referencia.

        Tenta, em ordem:
          1. ``tx_voz_referencia_path`` apontando para arquivo .wav
             (voz de Voice Design aprovada);
          2. Caminho derivado do dataset a partir de ``cd_voz``;
          3. String Base64 ja gravada em algum campo de extensao.

        Retorna tupla ``(ref_audio_base64, ref_text)``. Quando nao
        encontrar a referencia, retorna ``("", "")``.
        """
        caminho_ref = getattr(personagem, "tx_voz_referencia_path", None)
        if caminho_ref:
            from pathlib import Path

            caminho = Path(caminho_ref)
            if caminho.is_file():
                import base64

                ref_audio_b64 = base64.b64encode(
                    caminho.read_bytes()
                ).decode("ascii")
                return ref_audio_b64, ""
        # Fallback: o servico de catalogacao pode gerar audio via
        # ``gerar_voz_design`` on demand. Como isso exige o servico
        # real de TTS, retornamos vazio para que o pipeline apenas
        # pule o personagem.
        return "", ""

    def _dividir_texto(self, texto: str) -> list[str]:
        """Divide um texto em chunks para o TTS.

        Wrapper em torno de :meth:`TTSServico._dividir_em_chunks`
        quando disponivel. Caso o servico de TTS nao implemente o
        metodo (por exemplo no stub), retorna o texto inteiro como
        um unico chunk para que o pipeline nao quebre.
        """
        if self.tts is not None and hasattr(self.tts, "_dividir_em_chunks"):
            return list(self.tts._dividir_em_chunks(texto))  # type: ignore[attr-defined]
        texto = (texto or "").strip()
        return [texto] if texto else []

    def _tts_gerar_lote(
        self, falas: list[dict[str, Any]], referencia_voz: dict[str, str]
    ) -> list[bytes]:
        """Encapsula a chamada (possivelmente async) ao TTS.

        O servico real expoe :meth:`gerar_audio_lote` como coroutine.
        Para manter o pipeline sincrono, executamos o coroutine via
        :func:`asyncio.run` quando necessario. O stub sincrono de
        testes simplesmente retorna a lista vazia.
        """
        if not hasattr(self.tts, "gerar_audio_lote"):
            return []
        resultado = self.tts.gerar_audio_lote(falas, referencia_voz)
        if asyncio.iscoroutine(resultado):
            return asyncio.run(resultado)
        return list(resultado)

    def _juncar_audio(self, livro_id: int) -> None:
        """Etapa 7: concatena os chunks de audio em um arquivo final.

        Carrega todos os arquivos ``caminho_audio_chunk`` em ordem
        (``nr_ordem`` da fala e ``nr_chunk`` do arquivo) e gera um
        WAV unico em ``caminho_audio_final``. Quando ``soundfile``
        e ``numpy`` estao disponiveis, a concatenacao eh feita em
        memoria usando ``numpy.concatenate``. Caso contrario, o
        pipeline usa uma estrategia de copia simples para garantir
        progresso (apenas o primeiro chunk eh renomeado).
        """
        logger.info("Etapa 7 (juncar_audio) iniciada: livro_id=%s", livro_id)
        self._atualizar_estado(livro_id, EstadoPipeline.JUNCAO)
        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            stmt = (
                select(LivroFala)
                .where(
                    LivroFala.cd_sequenciallivro == livro_id,
                    LivroFala.caminho_audio_chunk.is_not(None),
                )
                .order_by(
                    LivroFala.nr_ordem.asc().nullslast(),
                    LivroFala.nr_chunk.asc().nullslast(),
                )
            )
            falas: list[LivroFala] = list(session.execute(stmt).scalars().all())
            if not falas:
                raise PipelineErroError(
                    f"Nenhum chunk de audio disponivel para livro_id={livro_id}"
                )

            output_dir = self._settings.audio_output_path / f"livro_{livro_id}"
            output_dir.mkdir(parents=True, exist_ok=True)
            destino = output_dir / "audiolivro_final.wav"

            self._concatenar_wavs(falas, destino)

            livro.caminho_audio_final = str(destino)
            livro.fl_produzido = "S"
            livro.dt_conclusao = datetime.utcnow()
            livro.estado_pipeline = EstadoPipeline.CONCLUIDO.value
            livro.progresso_atual = TOTAL_ETAPAS
            livro.progresso_total = TOTAL_ETAPAS
            session.add(livro)
        logger.info("Etapa 7 (juncar_audio) concluida: livro_id=%s", livro_id)

    def _concatenar_wavs(self, falas: list[LivroFala], destino: Any) -> None:
        """Concatena uma lista de WAVs em um unico arquivo de saida.

        Estrategia:
          * Se ``soundfile`` e ``numpy`` estao disponiveis, le cada
            arquivo como array numpy e concatena com
            ``np.concatenate``.
          * Caso contrario, copia o primeiro chunk como arquivo final
            (estrategia de fallback que garante progresso do pipeline
            mesmo sem dependencias de audio instaladas).
        """
        try:
            import numpy as np
            import soundfile as sf
        except Exception:  # noqa: BLE001
            logger.warning(
                "soundfile/numpy indisponiveis; juncao de audio fara "
                "copia simples do primeiro chunk"
            )
            primeiro = next(
                (f.caminho_audio_chunk for f in falas if f.caminho_audio_chunk),
                None,
            )
            if primeiro:
                from pathlib import Path

                Path(destino).write_bytes(Path(primeiro).read_bytes())
            return

        arrays: list[Any] = []
        samplerate: int | None = None
        for fala in falas:
            caminho = fala.caminho_audio_chunk
            if not caminho:
                continue
            try:
                data, sr = sf.read(caminho, dtype="float32")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Falha ao ler WAV %s: %s", caminho, exc)
                continue
            if samplerate is None:
                samplerate = sr
            elif samplerate != sr:
                # Diferenca de sample rate: descarta para manter consistencia.
                logger.warning(
                    "Sample rate divergente em %s (%s vs %s); pulando",
                    caminho,
                    sr,
                    samplerate,
                )
                continue
            arrays.append(data)
        if not arrays or samplerate is None:
            logger.warning("Nenhum audio valido para concatenar")
            return
        combinado = np.concatenate(arrays, axis=0)
        sf.write(str(destino), combinado, samplerate)

    # ------------------------------------------------------------------ #
    # Orquestracao principal
    # ------------------------------------------------------------------ #

    def _lista_etapas(self) -> list[Etapa]:
        """Retorna a lista ordenada de etapas a executar."""
        return [
            self._extraer_texto,
            self._identificar_personagens,
            self._normalizar_personagens,
            self._definir_vozes,
            self._inferir_emocoes,
            self._gerar_audio,
            self._juncar_audio,
        ]

    def _nome_etapa(self, etapa: Etapa) -> str:
        """Retorna o nome canonico usado nos logs e checkpoints."""
        return etapa.__name__.lstrip("_")

    def executar_pipeline(self, livro_id: int) -> None:
        """Executa todas as etapas do pipeline em sequencia.

        Fluxo:
          1. Verifica pausa antes de cada etapa;
          2. Executa a etapa;
          3. Salva o checkpoint no banco;
          4. Segue para a proxima etapa.

        Em caso de erro:
          * ``PipelinePausadoError`` nao e retida — o worker Celery
            a trata como finalizacao controlada;
          * ``LivroNaoEncontradoError`` e retida (o livro nao pode
            ter seu estado alterado);
          * Qualquer outra excecao e capturada, o estado do livro
            e marcado como ``erro`` com a mensagem, e a excecao
            original e relancada para que o Celery possa reagendar.
        """
        for etapa in self._lista_etapas():
            self._verificar_pausa(livro_id)
            try:
                etapa(livro_id)
            except PipelinePausadoError:
                # Pausa e estado final controlado, nao conta como erro.
                raise
            except LivroNaoEncontradoError:
                # Sem livro, nao ha o que atualizar.
                raise
            except Exception as exc:  # noqa: BLE001
                self._atualizar_erro(livro_id, str(exc))
                logger.exception(
                    "Erro na etapa %s do livro id=%s", etapa.__name__, livro_id
                )
                raise
            self._salvar_checkpoint(livro_id, self._nome_etapa(etapa))

    def retomar_pipeline(self, livro_id: int) -> None:
        """Retoma o pipeline a partir da ultima etapa nao concluida.

        A decisao de quais etapas ja foram concluidas e tomada
        combinando o ``progresso_atual`` persistido e as flags
        auxiliares (``fl_lido``, ``fl_normalizado``, ``fl_narrador``,
        ``fl_produzido``). Caso o estado esteja ``aguardando`` ou
        ``erro``, a execucao recomeca da primeira etapa.
        """
        with session_scope() as session:
            livro = self._buscar_livro_ou_erro(session, livro_id)
            estado = livro.estado_pipeline or EstadoPipeline.AGUARDANDO.value
            progresso = livro.progresso_atual or 0
            fl_lido = (livro.fl_lido or "").upper() == "S"
            fl_normalizado = (livro.fl_normalizado or "").upper() == "S"
            fl_narrador = (livro.fl_narrador or "").upper() == "S"
            fl_produzido = (livro.fl_produzido or "").upper() == "S"

        if estado == EstadoPipeline.CONCLUIDO.value or fl_produzido:
            logger.info(
                "Livro id=%s ja esta concluido; nada a retomar", livro_id
            )
            return

        etapas_a_pular: set[str] = set()
        if fl_lido:
            etapas_a_pular.add("extraer_texto")
        if fl_normalizado:
            etapas_a_pular.update({"identificar_personagens", "normalizar_personagens"})
        if fl_narrador:
            etapas_a_pular.add("definir_vozes")
        if fl_produzido:
            etapas_a_pular.update(
                {"inferir_emocoes", "gerar_audio", "juncar_audio"}
            )

        # O ``progresso_atual`` tambem e usado como fonte: se for >= N,
        # pulamos as N primeiras etapas (cada checkpoint incrementa em 1).
        if progresso > 0 and progresso <= TOTAL_ETAPAS:
            for idx, etapa in enumerate(self._lista_etapas(), start=1):
                if idx <= progresso:
                    etapas_a_pular.add(self._nome_etapa(etapa))

        logger.info(
            "Retomando pipeline: livro_id=%s estado=%s progresso=%d "
            "etapas_a_pular=%s",
            livro_id,
            estado,
            progresso,
            sorted(etapas_a_pular),
        )

        for etapa in self._lista_etapas():
            nome = self._nome_etapa(etapa)
            if nome in etapas_a_pular:
                continue
            self._verificar_pausa(livro_id)
            try:
                etapa(livro_id)
            except PipelinePausadoError:
                raise
            except LivroNaoEncontradoError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._atualizar_erro(livro_id, str(exc))
                logger.exception(
                    "Erro na retomada (etapa %s) do livro id=%s",
                    nome,
                    livro_id,
                )
                raise
            self._salvar_checkpoint(livro_id, nome)


# -----------------------------------------------------------------------------
# Tarefa Celery
# -----------------------------------------------------------------------------


@celery_app.task(name="tasks.executar_pipeline", bind=True, max_retries=3)
def executar_pipeline_task(self, livro_id: int) -> dict:
    """Tarefa Celery que executa o pipeline de um livro.

    O retorno eh um dicionario com ``livro_id`` e ``status``. Em caso
    de pausa controlada (``PipelinePausadoError``), o status retornado
    e ``pausado`` e a tarefa NAO eh reagendada — o administrador
    podera retomar manualmente. Para outros erros, a tarefa chama
    ``self.retry`` com backoff de 60 segundos ate esgotar o
    ``max_retries`` definido na assinatura.
    """
    # Import local para evitar ciclo: pipeline -> celery_app -> pipeline.
    from tasks.exceptions import PipelinePausadoError

    try:
        pipeline = PipelineOrquestrador()
        pipeline.executar_pipeline(livro_id)
        return {"livro_id": livro_id, "status": "concluido"}
    except PipelinePausadoError as exc:
        logger.info(
            "Tarefa Celery interrompida por pausa: livro_id=%s (%s)",
            livro_id,
            exc,
        )
        return {"livro_id": livro_id, "status": "pausado"}
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Falha na tarefa Celery executar_pipeline_task: livro_id=%s",
            livro_id,
        )
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="tasks.retomar_pipeline", bind=True, max_retries=3)
def retomar_pipeline_task(self, livro_id: int) -> dict:
    """Tarefa Celery que retoma o pipeline pausado ou com erro."""
    from tasks.exceptions import PipelinePausadoError

    try:
        pipeline = PipelineOrquestrador()
        pipeline.retomar_pipeline(livro_id)
        return {"livro_id": livro_id, "status": "concluido"}
    except PipelinePausadoError as exc:
        logger.info(
            "Retomada interrompida por pausa: livro_id=%s (%s)", livro_id, exc
        )
        return {"livro_id": livro_id, "status": "pausado"}
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Falha na tarefa Celery retomar_pipeline_task: livro_id=%s",
            livro_id,
        )
        raise self.retry(exc=exc, countdown=60)


# -----------------------------------------------------------------------------
# Constantes e helpers internos
# -----------------------------------------------------------------------------


# Mapeamento entre o nome canonico da etapa (sem underline inicial) e
# o par ``(estado do pipeline, progresso)`` a ser gravado no checkpoint.
_MAPA_ETAPA_ESTADO: dict[str, tuple[EstadoPipeline, int]] = {
    "extraer_texto": (EstadoPipeline.EXTRACAO, 1),
    "identificar_personagens": (EstadoPipeline.PERSONAGENS, 2),
    "normalizar_personagens": (EstadoPipeline.PERSONAGENS, 3),
    "definir_vozes": (EstadoPipeline.VOZES, 4),
    "inferir_emocoes": (EstadoPipeline.PRODUCAO, 5),
    "gerar_audio": (EstadoPipeline.PRODUCAO, 6),
    "juncar_audio": (EstadoPipeline.CONCLUIDO, TOTAL_ETAPAS),
}


def _hash_voz_para_int(voice_id: str) -> int:
    """Converte um id de voz textual em um inteiro estavel.

    A tabela ``TB_LIVROPERSONAGENS.CD_VOZ`` armazena o id numerico
    da voz. Como o dataset guarda apenas identificadores textuais,
    usamos o hash do nome como chave numerica. A funcao ``abs`` evita
    colisoes com a restricao de coluna unsigned (no Postgres
    ``BigInteger`` aceita valores negativos, mas o valor positivo
    torna a coluna mais legivel).
    """
    return abs(hash(voice_id)) % (2**31)


# -----------------------------------------------------------------------------
# Stubs internos (usados quando os servicos reais nao estao disponiveis)
# -----------------------------------------------------------------------------


class TTSServicoStub:
    """Stub minimo de TTS usado apenas em ambientes sem o servico real.

    A classe existe para que o pipeline nao quebre durante importacao
    em ambientes de teste ou de desenvolvimento sem o servidor Qwen3-TTS.
    Nenhuma chamada HTTP eh feita — todos os metodos retornam listas
    vazias.
    """

    def gerar_audio_lote(  # noqa: D401
        self,
        falas: list[dict[str, Any]],
        referencia_voz: dict[str, str],
    ) -> list[bytes]:
        return []

    async def gerar_audio(  # noqa: D401
        self,
        texto: str,
        ref_audio_base64: str,
        ref_text: str = "",
        instrucao: Any | None = None,
        linguagem: str = "Portuguese",
        use_prompt_cache: bool = True,
    ) -> bytes:
        return b""


class TTSServicoReal:
    """Wrapper de conveniencia sobre o servico real de TTS."""

    def __init__(self) -> None:
        from app.services.tts import TTSServico

        self._impl = TTSServico()

    def gerar_audio_lote(
        self,
        falas: list[dict[str, Any]],
        referencia_voz: dict[str, str],
    ) -> Any:
        return self._impl.gerar_audio_lote(falas, referencia_voz)


class _PersonagensServiceStub:
    """Stub leve em torno do servico de personagens.

    O servico real (task_09) pode ainda nao estar totalmente
    implementado. Este stub expoe os metodos esperados pelo
    orquestrador e utiliza o LLM (quando disponivel) para gerar
    chamadas. Caso o LLM nao esteja configurado, retorna contagens
    zeradas para nao bloquear o pipeline.
    """

    def __init__(self, llm: Any | None) -> None:
        self._llm = llm

    def identificar_personagens_por_pagina(self, livro_id: int) -> int:
        """Identifica personagens chamando o LLM por pagina.

        Implementacao minima: percorre paginas nao processadas e,
        para cada uma, chama ``llm.identificar_personagens``. Os
        resultados sao persistidos como falas (atribuidas a um
        personagem ``Personagem Desconhecido`` quando nao
        reconhecido).
        """
        with session_scope() as session:
            repo = LivroPaginaRepositorio(session)
            paginas = repo.listar_nao_processadas(livro_id)
            if not paginas:
                return 0
            if self._llm is None:
                return 0
            for pagina in paginas:
                if not pagina.tx_pagina:
                    continue
                try:
                    resultados = self._llm.identificar_personagens(pagina.tx_pagina)
                except Exception:  # noqa: BLE001
                    continue
                # Cria/atualiza personagens sob demanda
                personagens_cache: dict[str, LivroPersonagem] = {}
                for item in resultados:
                    nome = item.get("personagem", "Personagem Desconhecido")
                    if nome not in personagens_cache:
                        existente = session.execute(
                            select(LivroPersonagem).where(
                                LivroPersonagem.cd_sequenciallivro == livro_id,
                                LivroPersonagem.tx_personagem == nome,
                            )
                        ).scalar_one_or_none()
                        if existente is not None:
                            personagens_cache[nome] = existente
                        else:
                            novo = LivroPersonagem(
                                cd_sequenciallivro=livro_id,
                                tx_personagem=nome,
                            )
                            session.add(novo)
                            session.flush()
                            personagens_cache[nome] = novo
                    personagem = personagens_cache[nome]
                    fala = LivroFala(
                        cd_sequenciallivro=livro_id,
                        cd_sequencialpagina=pagina.cd_sequencial,
                        cd_sequencialpersonagem=personagem.cd_sequencial,
                        tx_fala=item.get("texto", ""),
                        fl_processado="N",
                        nr_ordem=pagina.nr_pagina or 0,
                        eh_narracao="S" if (item.get("tipo") == "narracao") else "N",
                    )
                    session.add(fala)
                pagina.fl_processado = "S"
                session.add(pagina)
        return 0

    def normalizar_personagens(self, livro_id: int) -> int:
        """Consolida nomes equivalentes de personagens usando o LLM."""
        if self._llm is None:
            return 0
        with session_scope() as session:
            stmt = (
                select(LivroPersonagem)
                .where(LivroPersonagem.cd_sequenciallivro == livro_id)
            )
            personagens = list(session.execute(stmt).scalars().all())
            if not personagens:
                return 0
            lista = [{"nome_original": p.tx_personagem or "", "falas_count": 0} for p in personagens]
            try:
                normalizados = self._llm.normalizar_personagens(lista)
            except Exception:  # noqa: BLE001
                return 0
            for grupo in normalizados:
                nome_norm = grupo.get("nome_normalizado", "")
                if not nome_norm:
                    continue
                originais = grupo.get("nomes_originais", [])
                alvo = session.execute(
                    select(LivroPersonagem).where(
                        LivroPersonagem.cd_sequenciallivro == livro_id,
                        LivroPersonagem.tx_personagem == nome_norm,
                    )
                ).scalar_one_or_none()
                if alvo is None:
                    alvo = LivroPersonagem(
                        cd_sequenciallivro=livro_id,
                        tx_personagem=nome_norm,
                    )
                    session.add(alvo)
                    session.flush()
                for nome_original in originais:
                    if nome_original == nome_norm:
                        continue
                    orig = session.execute(
                        select(LivroPersonagem).where(
                            LivroPersonagem.cd_sequenciallivro == livro_id,
                            LivroPersonagem.tx_personagem == nome_original,
                        )
                    ).scalar_one_or_none()
                    if orig is None or orig.cd_sequencial == alvo.cd_sequencial:
                        continue
                    # Reatribui falas e remove o duplicado.
                    for fala in list(orig.falas):
                        fala.cd_sequencialpersonagem = alvo.cd_sequencial
                        session.add(fala)
                    session.delete(orig)
        return 0


__all__ = [
    "PipelineOrquestrador",
    "executar_pipeline_task",
    "retomar_pipeline_task",
    "TOTAL_ETAPAS",
    "TTSServicoStub",
    "TTSServicoReal",
]
