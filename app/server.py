import grpc
from concurrent import futures
import threading
import queue
import logging
import time

import service_pb2 as pb2
import service_pb2_grpc as pb2_grpc


class ChatService(pb2_grpc.ChatServiceServicer):
    def __init__(self):
        self.clients: dict[str, queue.Queue] = {}
        self.lock = threading.Lock()
        logging.info("💬 ChatService initialized")

    def ChatStream(self, request_iterator, context):
        username = None
        outgoing_queue: queue.Queue = queue.Queue()

        def read_incoming():
            nonlocal username
            try:
                for request in request_iterator:
                    if not username:
                        username = request.username.strip() or f"User_{id(context)}"
                        with self.lock:
                            self.clients[username] = outgoing_queue
                        logging.info(f"🟢 Подключился: {username}")
                        # Отправляем всем КРОМЕ нового пользователя
                        self._broadcast("SERVER", f"{username} зашел в чат", exclude=username)
                    else:
                        logging.info(f"📨 {username}: {request.text}")
                        # Отправляем всем КРОМЕ отправителя
                        self._broadcast(username, request.text, exclude=username)
                    
            except grpc.RpcError as e:
                logging.warning(f"⚠️ Ошибка чтения у {username}: {e}")
            except Exception as e:
                logging.error(f"❌ Критическая ошибка у {username}: {e}")
            finally:
                if username:
                    logging.info(f"🔴 Отключился: {username}")
                    self._broadcast("SERVER", f"{username} покинул чат", exclude=username)
                    with self.lock:
                        self.clients.pop(username, None)

        reader_thread = threading.Thread(target=read_incoming, daemon=True)
        reader_thread.start()

        try:
            while context.is_active():
                try:
                    msg = outgoing_queue.get(timeout=1.0)
                    yield msg
                except queue.Empty:
                    continue
        except grpc.RpcError as e:
            logging.warning(f"⚠️ Ошибка записи: {e}")
        finally:
            reader_thread.join(timeout=2.0)

    def _broadcast(self, sender: str, text: str, exclude: str = None):
        """Отправляет сообщение всем клиентам, кроме exclude"""
        msg = pb2.ChatMessage(username=sender, text=text)
        with self.lock:
            for user_name, q in list(self.clients.items()):
                if exclude and user_name == exclude:
                    continue  # Пропускаем отправителя
                try:
                    q.put(msg)
                except Exception:
                    pass


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=100))
    pb2_grpc.add_ChatServiceServicer_to_server(ChatService(), server)
    server.add_insecure_port('[::]:50051')
    
    server.start()
    logging.info("✅ Сервер чата запущен на [::]:50051")
    logging.info("🌐 Доступен из локальной сети и Tailscale")
    
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logging.info("🛑 Получен сигнал остановки, завершаем работу...")
        server.stop(0)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    serve()