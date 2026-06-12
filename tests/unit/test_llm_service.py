"""Testes unitarios para ``app.services.llm.LLMServico``.

Cobre:
* Construcao com defaults vs. parametros explicitos;
* Retry com backoff em erros transientes (APITimeoutError,
  APIConnectionError, InternalServerError);
* Levantamento de LLMErroError apos esgotar tentativas;
* Erros nao-transientes sao levantados imediatamente;
* Wrapper generico ``chamar_llm``;
* Parsing de respostas de identificacao, normalizacao e emocao.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import openai
import pytest

from app.services.llm import LLMErroError, LLMServico


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_completion(content: str) -> MagicMock:
    """Monta um mock de resposta ``chat.completions.create``.

    Atende ao contrato esperado por ``LLMServico._chamar_llm``:
    ``response.choices[0].message.content``.
    """
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    completion = MagicMock()
    completion.choices = [choice]
    return completion


def _make_client_mock(content: str) -> MagicMock:
    """Monta um mock de ``OpenAI`` retornando ``content`` na primeira chamada."""
    client = MagicMock()
    client.chat.completions.create.return_value = _make_completion(content)
    return client


@pytest.fixture
def servico_padrao() -> LLMServico:
    """Instancia o servico com todos os parametros explicitos (sem rede)."""
    return LLMServico(
        base_url="http://llm.test/v1/",
        api_key="key-test",
        model="modelo-teste",
        timeout=5,
        max_retries=3,
    )


# --------------------------------------------------------------------------- #
# Construtor
# --------------------------------------------------------------------------- #


class TestConstrutor:
    """Verifica leitura de configuracoes e instancia do cliente OpenAI."""

    def test_parametros_explicitos_sao_usados(self) -> None:
        servico = LLMServico(
            base_url="http://x.test/v1/",
            api_key="abc",
            model="m",
            timeout=10,
            max_retries=5,
        )
        assert servico.base_url == "http://x.test/v1/"
        assert servico.api_key == "abc"
        assert servico.model == "m"
        assert servico.timeout == 10
        assert servico.max_retries == 5

    def test_instancia_cliente_openai_com_timeout_e_sem_retry_interno(
        self, servico_padrao: LLMServico
    ) -> None:
        with patch("app.services.llm.OpenAI") as openai_cls:
            LLMServico(
                base_url="http://llm.test/v1/",
                api_key="key-test",
                model="modelo-teste",
                timeout=5,
                max_retries=3,
            )
        openai_cls.assert_called_once_with(
            base_url="http://llm.test/v1/",
            api_key="key-test",
            timeout=5,
            max_retries=0,
        )

    def test_le_settings_quando_parametros_sao_none(self) -> None:
        """Se todos os params forem None, deve usar ``get_settings()``."""
        with patch("app.services.llm.OpenAI") as openai_cls:
            openai_cls.return_value = MagicMock()
            LLMServico()  # sem argumentos
        # Verifica que o construtor do OpenAI foi chamado com os valores
        # vindos de Settings (defaults do projeto).
        openai_cls.assert_called_once()
        kwargs = openai_cls.call_args.kwargs
        assert kwargs["base_url"] == "http://192.168.2.112:8000/v1/"
        assert kwargs["api_key"] == "local"
        assert kwargs["timeout"] == 60
        assert kwargs["max_retries"] == 0


# --------------------------------------------------------------------------- #
# Retry / backoff
# --------------------------------------------------------------------------- #


class TestRetry:
    """Cobertura do mecanismo de retry com backoff exponencial."""

    def test_retorna_resposta_em_caso_de_sucesso(
        self, servico_padrao: LLMServico
    ) -> None:
        servico_padrao._client = _make_client_mock("ok")
        with patch("app.services.llm.time.sleep") as sleep_mock:
            resposta = servico_padrao._chamar_llm(
                [{"role": "user", "content": "oi"}]
            )
        assert resposta == "ok"
        # Nenhum sleep eh esperado quando a primeira tentativa da certo.
        sleep_mock.assert_not_called()
        servico_padrao._client.chat.completions.create.assert_called_once()

    def test_retry_em_timeout_com_sucesso_posteriormente(
        self, servico_padrao: LLMServico
    ) -> None:
        request_mock = MagicMock()
        client_mock = MagicMock()
        client_mock.chat.completions.create.side_effect = [
            openai.APITimeoutError(request=request_mock),
            openai.APITimeoutError(request=request_mock),
            _make_completion("resposta final"),
        ]
        servico_padrao._client = client_mock
        with patch("app.services.llm.time.sleep") as sleep_mock:
            resposta = servico_padrao._chamar_llm(
                [{"role": "user", "content": "oi"}]
            )
        assert resposta == "resposta final"
        # 2 sleeps (2s e 4s — duas falhas antes do sucesso).
        assert sleep_mock.call_count == 2
        assert sleep_mock.call_args_list[0].args == (2,)
        assert sleep_mock.call_args_list[1].args == (4,)
        assert client_mock.chat.completions.create.call_count == 3

    def test_retry_em_erros_5xx_e_conexao(
        self, servico_padrao: LLMServico
    ) -> None:
        request_mock = MagicMock()
        response_mock = MagicMock()
        client_mock = MagicMock()
        client_mock.chat.completions.create.side_effect = [
            openai.APIConnectionError(message="rede", request=request_mock),
            openai.InternalServerError(
                message="erro 500", response=response_mock, body=None
            ),
            _make_completion("ok"),
        ]
        servico_padrao._client = client_mock
        with patch("app.services.llm.time.sleep") as sleep_mock:
            resposta = servico_padrao._chamar_llm(
                [{"role": "user", "content": "oi"}]
            )
        assert resposta == "ok"
        assert sleep_mock.call_count == 2

    def test_levanta_erro_apos_esgotar_tentativas(
        self, servico_padrao: LLMServico
    ) -> None:
        client_mock = MagicMock()
        client_mock.chat.completions.create.side_effect = openai.APITimeoutError(
            request=MagicMock()
        )
        servico_padrao._client = client_mock
        with patch("app.services.llm.time.sleep"):
            with pytest.raises(LLMErroError) as excinfo:
                servico_padrao._chamar_llm(
                    [{"role": "user", "content": "oi"}]
                )
        # Mensagem deve incluir o numero de tentativas e o erro original.
        assert "3 tentativas" in str(excinfo.value)
        assert "APITimeoutError" in str(excinfo.value)
        # max_retries=3 -> 3 tentativas -> 2 sleeps (entre tentativas).
        assert client_mock.chat.completions.create.call_count == 3

    def test_erro_nao_transiente_nao_retry(
        self, servico_padrao: LLMServico
    ) -> None:
        client_mock = MagicMock()
        client_mock.chat.completions.create.side_effect = openai.AuthenticationError(
            message="401 unauthorized",
            response=MagicMock(),
            body=None,
        )
        servico_padrao._client = client_mock
        with patch("app.services.llm.time.sleep") as sleep_mock:
            with pytest.raises(LLMErroError):
                servico_padrao._chamar_llm(
                    [{"role": "user", "content": "oi"}]
                )
        # AuthenticationError nao eh transiente: uma unica tentativa.
        assert client_mock.chat.completions.create.call_count == 1
        sleep_mock.assert_not_called()

    def test_backoff_respeita_max_retries_1(self) -> None:
        """Com max_retries=1, nao ha sleep (nenhuma retry eh tentada)."""
        servico = LLMServico(
            base_url="http://x/",
            api_key="k",
            model="m",
            timeout=1,
            max_retries=1,
        )
        client_mock = MagicMock()
        client_mock.chat.completions.create.side_effect = openai.APITimeoutError(
            request=MagicMock()
        )
        servico._client = client_mock
        with patch("app.services.llm.time.sleep") as sleep_mock:
            with pytest.raises(LLMErroError):
                servico._chamar_llm([{"role": "user", "content": "x"}])
        assert client_mock.chat.completions.create.call_count == 1
        sleep_mock.assert_not_called()

    def test_conteudo_none_levanta_erro(
        self, servico_padrao: LLMServico
    ) -> None:
        servico_padrao._client = _make_client_mock(None)  # type: ignore[arg-type]
        # _make_completion(None) faz message.content = None.
        with patch("app.services.llm.time.sleep"):
            with pytest.raises(LLMErroError) as excinfo:
                servico_padrao._chamar_llm(
                    [{"role": "user", "content": "oi"}]
                )
        assert "content=None" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# chamar_llm (wrapper publico)
# --------------------------------------------------------------------------- #


class TestChamarLlm:
    def test_encaminha_prompt_para_chat_completions(
        self, servico_padrao: LLMServico
    ) -> None:
        servico_padrao._client = _make_client_mock("ok")
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.chamar_llm("meu prompt")
        assert resultado == "ok"
        servico_padrao._client.chat.completions.create.assert_called_once()
        kwargs = servico_padrao._client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "modelo-teste"
        assert kwargs["messages"] == [{"role": "user", "content": "meu prompt"}]
        assert kwargs["max_tokens"] == 4096
        assert kwargs["temperature"] == 0.1


# --------------------------------------------------------------------------- #
# identificar_personagens + _parsear_resposta
# --------------------------------------------------------------------------- #


class TestIdentificarPersonagens:
    def test_identifica_falas_e_narracao(self, servico_padrao: LLMServico) -> None:
        resposta_llm = (
            "Narrador|Era uma vez um reino distante.\n"
            "Maria|Boa noite, principe.\n"
            "Principe|Boa noite, Maria.\n"
            "Narrador|E ambos sorriram.\n"
        )
        servico_padrao._client = _make_client_mock(resposta_llm)
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.identificar_personagens("texto qualquer")
        assert resultado == [
            {
                "personagem": "Narrador",
                "texto": "Era uma vez um reino distante.",
                "tipo": "narracao",
            },
            {
                "personagem": "Maria",
                "texto": "Boa noite, principe.",
                "tipo": "fala",
            },
            {
                "personagem": "Principe",
                "texto": "Boa noite, Maria.",
                "tipo": "fala",
            },
            {
                "personagem": "Narrador",
                "texto": "E ambos sorriram.",
                "tipo": "narracao",
            },
        ]

    def test_descarta_linhas_invalidas(self, servico_padrao: LLMServico) -> None:
        resposta_llm = (
            "linha sem pipe deve ser ignorada\n"
            "Maria|texto valido\n"
            "|texto sem nome\n"  # sem nome -> vira "Personagem Desconhecido"
            "Pedro|\n"  # sem texto -> descartada
            "Narrador|Narracao valida\n"
        )
        servico_padrao._client = _make_client_mock(resposta_llm)
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.identificar_personagens("...")
        assert len(resultado) == 3
        assert resultado[0]["personagem"] == "Maria"
        assert resultado[0]["tipo"] == "fala"
        assert resultado[1]["personagem"] == "Personagem Desconhecido"
        assert resultado[1]["texto"] == "texto sem nome"
        assert resultado[2]["personagem"] == "Narrador"
        assert resultado[2]["tipo"] == "narracao"

    def test_resposta_vazia_retorna_lista_vazia(
        self, servico_padrao: LLMServico
    ) -> None:
        servico_padrao._client = _make_client_mock("")
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.identificar_personagens("...")
        assert resultado == []


# --------------------------------------------------------------------------- #
# normalizar_personagens
# --------------------------------------------------------------------------- #


class TestNormalizarPersonagens:
    def test_parseia_agrupamentos(self, servico_padrao: LLMServico) -> None:
        resposta_llm = (
            "Maria|Maria;D. Maria;Maria, a Protagonista|"
            "Mesma personagem referida com diferentes graus de formalidade\n"
            "Principe|Príncipe|Príncipe em todos os trechos\n"
        )
        servico_padrao._client = _make_client_mock(resposta_llm)
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.normalizar_personagens(
                [
                    {"nome_original": "Maria", "falas_count": 12},
                    {"nome_original": "D. Maria", "falas_count": 3},
                    {"nome_original": "Principe", "falas_count": 5},
                ]
            )
        assert len(resultado) == 2
        assert resultado[0]["nome_normalizado"] == "Maria"
        assert "D. Maria" in resultado[0]["nomes_originais"]
        assert "Maria" in resultado[0]["nomes_originais"]
        assert "Mesma personagem" in resultado[0]["justificativa"]
        assert resultado[1]["nome_normalizado"] == "Principe"

    def test_ignora_linhas_malformadas(self, servico_padrao: LLMServico) -> None:
        resposta_llm = (
            "nome_normalizado|nomes_originais|justificativa\n"  # cabecalho
            "Maria|Maria|Sozinha\n"
            "linha sem pipe\n"
            "|SemNome|Sem nome canonico\n"
        )
        servico_padrao._client = _make_client_mock(resposta_llm)
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.normalizar_personagens(
                [{"nome_original": "Maria", "falas_count": 1}]
            )
        assert len(resultado) == 1
        assert resultado[0]["nome_normalizado"] == "Maria"

    def test_garante_nome_normalizado_presente_nos_originais(
        self, servico_padrao: LLMServico
    ) -> None:
        resposta_llm = "Capitao|Capitao;Capitão|Forma preferida em PT-BR\n"
        servico_padrao._client = _make_client_mock(resposta_llm)
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.normalizar_personagens(
                [{"nome_original": "Capitao", "falas_count": 1}]
            )
        assert resultado[0]["nome_normalizado"] == "Capitao"
        assert "Capitao" in resultado[0]["nomes_originais"]


# --------------------------------------------------------------------------- #
# inferir_emocao
# --------------------------------------------------------------------------- #


class TestInferirEmocao:
    def test_parseia_campos_basicos(self, servico_padrao: LLMServico) -> None:
        resposta_llm = (
            "EMOCAO: fale de forma alegre e saltitante\n"
            "PROSODIA: fale bem devagar, fazendo pausas dramaticas\n"
            "PARALINGUISTICA: [sigh]\n"
        )
        servico_padrao._client = _make_client_mock(resposta_llm)
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.inferir_emocao("Ola, mundo!")
        assert resultado == {
            "emocao": "fale de forma alegre e saltitante",
            "prosodia": "fale bem devagar, fazendo pausas dramaticas",
            "paralinguistica": "[sigh]",
        }

    def test_paralinguistica_vazia(self, servico_padrao: LLMServico) -> None:
        resposta_llm = (
            "EMOCAO: fale com raiva contida\n"
            "PROSODIA: fale rapido e firme\n"
            "PARALINGUISTICA: (vazio)\n"
        )
        servico_padrao._client = _make_client_mock(resposta_llm)
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.inferir_emocao("Saiam daqui!")
        assert resultado["emocao"] == "fale com raiva contida"
        assert resultado["prosodia"] == "fale rapido e firme"
        assert resultado["paralinguistica"] == ""

    def test_resposta_vazia_retorna_dict_vazio(
        self, servico_padrao: LLMServico
    ) -> None:
        servico_padrao._client = _make_client_mock("")
        with patch("app.services.llm.time.sleep"):
            resultado = servico_padrao.inferir_emocao("...")
        assert resultado == {"emocao": "", "prosodia": "", "paralinguistica": ""}

    def test_contexto_e_incluido_no_prompt(
        self, servico_padrao: LLMServico
    ) -> None:
        servico_padrao._client = _make_client_mock(
            "EMOCAO: calma\nPROSODIA: pausada\nPARALINGUISTICA: \n"
        )
        with patch("app.services.llm.time.sleep"):
            servico_padrao.inferir_emocao("Fala X", contexto="Contexto Y")
        # Verifica que o contexto aparece no prompt enviado.
        kwargs = servico_padrao._client.chat.completions.create.call_args.kwargs
        prompt = kwargs["messages"][0]["content"]
        assert "Contexto Y" in prompt
        assert "Fala X" in prompt


# --------------------------------------------------------------------------- #
# Prompt helpers
# --------------------------------------------------------------------------- #


class TestPrompts:
    def test_prompt_identificacao_contem_texto_e_regras(
        self, servico_padrao: LLMServico
    ) -> None:
        prompt = servico_padrao._montar_prompt_identificacao("texto de teste")
        assert "texto de teste" in prompt
        assert "TTS" in prompt or "extenso" in prompt
        assert "Pipe" in prompt or "pipe" in prompt
        assert "Narrador" in prompt

    def test_prompt_normalizacao_contem_tabela(
        self, servico_padrao: LLMServico
    ) -> None:
        prompt = servico_padrao._montar_prompt_normalizacao(
            [
                {"nome_original": "Maria", "falas_count": 10},
                {"nome_original": "Principe", "falas_count": 5},
            ]
        )
        assert "Maria" in prompt
        assert "Principe" in prompt
        assert "10" in prompt
        assert "5" in prompt

    def test_prompt_emocao_sem_contexto(
        self, servico_padrao: LLMServico
    ) -> None:
        prompt = servico_padrao._montar_prompt_emocao("Fala isolada")
        assert "Fala isolada" in prompt
        # Bloco de contexto nao deve aparecer quando contexto == "".
        assert "CONTEXTO" not in prompt

    def test_prompt_emocao_com_contexto(
        self, servico_padrao: LLMServico
    ) -> None:
        prompt = servico_padrao._montar_prompt_emocao("Fala X", contexto="Cenario Y")
        assert "Fala X" in prompt
        assert "Cenario Y" in prompt
        assert "CONTEXTO" in prompt


# --------------------------------------------------------------------------- #
# Importacao do modulo e excecao customizada
# --------------------------------------------------------------------------- #


def test_importacao_publica() -> None:
    """Garante que ``LLMServico`` e ``LLMErroError`` sao importaveis."""
    from app.services.llm import LLMErroError as Erro
    from app.services.llm import LLMServico as Servico

    assert Servico is not None
    assert Erro is not None
    assert issubclass(Erro, RuntimeError)
