"""
Servidor persistente para o Rumba que mantém sessões ativas.
Este script deve ser executado como processo Python 32-bit.
"""
from typing import Dict, Any

from .api import RumbaAPI, ERROR_CODES

import traceback
import threading
import argparse
import logging
import socket
import time
import json

logger = logging.getLogger(__name__)

# Sessions gerenciadas pelo servidor
sessions: dict[str, RumbaAPI] = {}

def is_in_erro_grave(rumba: RumbaAPI) -> bool:
    check = "E R R O   G R A V E"
    try:
        return rumba.copy_ps_to_string(5, 31, len(check)) == check
    except Exception:
        return False

def screen_load(rumba: RumbaAPI,
                y: int,
                x: int,
                text: str,
                text_diff: bool = False) -> Dict[str, Any]:
    """Aguarda até que um texto específico apareça na tela"""
    logger.debug(f"Aguardando texto '{text}' na posição y={y}, x={x}")

    for i in range(120):  # 2 minutos de timeout
        try:
            check = rumba.copy_ps_to_string(y, x, len(text))
            condition = (check != text) if text_diff else (check == text)

            if condition:
                return {'success': True}

            if i > 0 and i % 15 == 0:
                logger.debug(f"Ainda aguardando texto '{text}', valor atual: '{check}'")
        except Exception as e:
            error_message = f"Erro na funcao screen_load: {e}"
            logger.error(error_message)
            return {'success': False, 'error': error_message}

        time.sleep(1)

        if (i % 5 == 0) and is_in_erro_grave(rumba):
            error_message = "CICS apresentou janela de erro grave!"
            logger.error(error_message)
            return {'success': False, 'error': error_message}

    error_message = f"Timeout aguardando texto '{text}'"
    logger.error(error_message)
    return {'success': False, 'error': error_message}

def logon_cics(rumba: RumbaAPI, uid: str, pwd: str) -> Dict[str, Any]:
    """Realiza login no terminal CICS"""
    try:
        logger.debug("Iniciando login no CICS")
        rumba.close_terminal()

        result = rumba.open_cics()
        if not result:
            logger.error('Falha ao abrir CICS')
            return {'success': False, 'error': 'Falha ao abrir CICS'}

        for i in range(61):
            result = rumba.wd_connect_ps()
            if result == 0:
                break
            assert i < 60, TimeoutError(
                'Nao foi possivel estabelecer uma conexao com o terminal RUMBA '
                f'codigo de erro: {result} - {ERROR_CODES['WD_ConnectPS'][result]}'
            )
            time.sleep(1)

        result = screen_load(rumba, 14, 33, 'CICS')
        if not result.get('success', False):
            return {'success': False, 'error': result.get('error')}

        rumba.copy_string_to_ps(22, 56, 'C')
        rumba.wd_send_key('ENTER')

        result = screen_load(rumba, 4, 6, 'TERMINAL')
        if not result.get('success', False):
            return {'success': False, 'error': result.get('error')}

        rumba.copy_string_to_ps(12, 21, uid)
        rumba.copy_string_to_ps(13, 21, pwd)
        rumba.wd_send_key('ENTER')

        # Verificar senha
        check = rumba.copy_ps_to_string(19, 19, 14)
        if "SENHA EXPIRADA" in check:
            logger.error("Senha do CICS expirada")
            return {'success': False, 'error': 'SENHA DO CICS ESTÁ EXPIRADA'}
        if "SENHA INVALIDA" in check:
            logger.error("Senha do CICS inválida")
            return {'success': False, 'error': 'SENHA DO CICS ESTÁ INCORRETA'}

        result = screen_load(rumba, 5, 20, "Signon")
        if not result.get('success', False):
            return {'success': False, 'error': result.get('error')}

        logger.debug("Login CICS realizado com sucesso")
        return {'success': True}
    except Exception as e:
        logger.error(f"Erro durante login: {str(e)}")
        logger.error(traceback.format_exc())
        return {'success': False, 'error': str(e)}

def logon_rhelp(rumba: RumbaAPI, uid: str, pwd: str) -> Dict[str, Any]:
    """Realiza login no terminal RHELP (IMS)"""
    try:
        logger.info("Iniciando login no Rhelp")
        rumba.close_terminal()

        result = rumba.open_rhelp()
        if not result:
            logger.error('Falha ao abrir RHELP')
            return {'success': False, 'error': 'Falha ao abrir RHELP'}

        rumba.wd_connect_ps()

        result = screen_load(rumba, 2, 1, 'USS010')
        if not result.get('success', False):
            return {'success': False, 'error': result.get('error')}

        rumba.copy_string_to_ps(5, 1, "IMSRS")
        rumba.wd_send_key("ENTER")

        result = screen_load(rumba, 4, 44, 'Welcome')
        if not result.get('success', False):
            return {'success': False, 'error': result.get('error')}

        rumba.copy_string_to_ps(10, 47, uid)
        rumba.copy_string_to_ps(11, 47, pwd)
        rumba.wd_send_key("ENTER")

        result = screen_load(rumba, 16, 20, 'Start')
        if not result.get('success', False):
            return {'success': False, 'error': result.get('error')}

        rumba.copy_string_to_ps(16, 47, "RHELP")
        rumba.wd_send_key("ENTER")

        logger.info("Login RHELP realizado com sucesso")
        return {'success': True}
    except Exception as e:
        logger.error(f"Erro durante login RHELP: {str(e)}")
        return {'success': False, 'error': str(e)}

def execute_command(session_id: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Executa um comando em uma sessão específica"""
    try:
        # Obter ou criar a sessão
        if session_id not in sessions:
            terminal_type = params.get('terminal_type', 'D')
            logger.info(f"Criando nova sessão {session_id} para terminal {terminal_type}")
            sessions[session_id] = RumbaAPI(gs_short_name_ims=terminal_type)

        rumba: RumbaAPI = sessions[session_id]

        logger.debug(f"Executando {action} na sessão {session_id}")

        if action == 'ping':
            return {'success': True, 'message': 'Server is running'}

        elif action == 'open_cics':
            result = rumba.open_cics()
            return {'success': True, 'result': result}

        elif action == 'open_rhelp':
            result = rumba.open_rhelp()
            return {'success': True, 'result': result}

        elif action == 'logon_cics':
            uid: int = params['uid']
            pwd: int = params['pwd']
            return logon_cics(rumba, uid, pwd)

        elif action == 'logon_rhelp':
            uid: int = params['uid']
            pwd: int = params['pwd']
            return logon_rhelp(rumba, uid, pwd)

        elif action == 'send_key':
            key: str = params['key']
            result = rumba.wd_send_key(key)
            return {'success': True, 'result': result}

        elif action == 'copy_ps_to_string':
            y: int = params['y']
            x: int = params['x']
            length: int = params['length']
            text: str = rumba.copy_ps_to_string(y, x, length)
            return {'success': True, 'text': text}

        elif action == 'copy_string_to_ps':
            y: int = params['y']
            x: int = params['x']
            text: str = params.get('text', '')
            rumba.copy_string_to_ps(y, x, text)
            return {'success': True}

        elif action == 'copy_field':
            y: int = params['y']
            x: int = params['x']
            text: str = rumba.copy_field(y, x)
            return {'success': True, 'text': text}

        elif action == 'screen_load':
            y: int = params['y']
            x: int = params['x']
            text: str = params['text']
            text_diff: bool = params.get('text_diff', False)
            result = screen_load(rumba, y, x, text, text_diff)
            return result

        elif action == 'close_terminal':
            result = rumba.close_terminal()
            return {'success': result}

        elif action == 'disconnect':
            rumba.wd_disconnect_ps()
            if session_id in sessions:
                del sessions[session_id]
            return {'success': True}

        elif action == 'connect':
            rumba.wd_connect_ps()
            return {'success': True}

        else:
            return {'success': False, 'error': f'Comando desconhecido: {action}'}
    except Exception as e:
        logger.error(f"Erro ao executar {action}: {str(e)}")
        logger.error(traceback.format_exc())
        return {'success': False, 'error': str(e)}

def handle_client(client_socket: socket.socket):
    data = ""
    try:
        # Receber dados
        chunks = []
        while True:
            chunk = client_socket.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)

        data = b''.join(chunks).decode('utf-8')

        # Processar comando
        try:
            request = json.loads(data)
            session_id = request.get('session_id', 'default')
            action = request.get('action')
            params = request.get('params', {})

            # Validar request
            if not action:
                response = json.dumps({
                    'success': False,
                    'error': 'Parâmetro "action" é obrigatório'
                })
                client_socket.send(response.encode('utf-8'))
                return

            # Executar o comando
            result = execute_command(session_id, action, params)

            # Enviar resposta
            response = json.dumps(result)
            client_socket.send(response.encode('utf-8'))
        except json.JSONDecodeError:
            logger.error(f"Erro ao decodificar solicitação JSON: {data}")
            response = json.dumps({
                'success': False, 
                'error': 'Formato de solicitação inválido'
            })
            client_socket.send(response.encode('utf-8'))
    except Exception as e:
        logger.error(f"Erro ao processar solicitação: {str(e)}")
        logger.error(f"Dados recebidos: {data}")
        try:
            response = json.dumps({
                'success': False,
                'error': f"Erro no servidor: {str(e)}"
            })
            client_socket.send(response.encode('utf-8'))
        except:
            pass
    finally:
        client_socket.close()

def start_server():
    parser = argparse.ArgumentParser(description='Servidor Rumba')
    parser.add_argument('--terminal_type', type=str, default='D',
                        choices=['D', 'A', 'Z'],
                        help='Tipo de terminal (D, A, Z)')
    args = parser.parse_args()
    terminal_type = args.terminal_type

    logger.info(f"Iniciando servidor Rumba para terminal tipo {terminal_type}...")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        # Configurações de conexão
        ports = {'D': 8500, 'A': 8600, 'Z': 8700}
        HOST = 'localhost'
        PORT = ports.get(terminal_type, 8800)

        server.bind((HOST, PORT))
        server.listen(5)
        logger.info(f"Servidor iniciado em {HOST}:{PORT}")

        while True:
            client, addr = server.accept()

            # Iniciar thread para lidar com o cliente
            client_thread = threading.Thread(target=handle_client, args=(client,))
            client_thread.daemon = True
            client_thread.start()
    except KeyboardInterrupt:
        logger.info("Servidor encerrado pelo usuário")
    except Exception as e:
        logger.error(f"Erro no servidor: {str(e)}")
    finally:
        # Limpar todas as sessões
        for session_id, rumba in list(sessions.items()):
            try:
                logger.info(f"Fechando sessão {session_id}")
                rumba.wd_disconnect_ps()
                rumba.close_terminal()
                del sessions[session_id]
            except Exception as e:
                logger.error(f"Erro ao fechar sessão {session_id}: {str(e)}")

        server.close()
        logger.debug("Servidor encerrado")

if __name__ == "__main__":
    start_server()