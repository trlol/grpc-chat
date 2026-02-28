import grpc
from concurrent import futures
import threading
import queue
import logging
import time
from datetime import datetime
import random
import requests
import os

import service_pb2 as pb2
import service_pb2_grpc as pb2_grpc

def translate_to_ru(text: str) -> str:
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "en",
                "tl": "ru",
                "dt": "t",
                "q": text,
            },
            timeout=5,
        )
        result = response.json()
        translated = "".join([item[0] for item in result[0]])
        return translated
    except Exception:
        return text

def get_random_fact():
    try:
        fact_response = requests.get(
            "https://uselessfacts.jsph.pl/random.json?language=en",
            timeout=5
        )
        fact_response.raise_for_status()
        fact = fact_response.json()["text"]

        russian_fact = translate_to_ru(fact)

        return f"📚 Факт: {russian_fact}"

    except Exception:
        return "⚠️ Не удалось получить факт"

# === СПИСОК КОМАНД ===
SERVER_COMMANDS = {
    '!время': lambda: f"🕐 Сейчас: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
    '!дата': lambda: f"📅 Сегодня: {datetime.now().strftime('%d.%m.%Y')}",
    '!часы': lambda: f"⏰ Время: {datetime.now().strftime('%H:%M')}",
    '!рандом': lambda: f"🎲 Случайное число: {random.randint(1, 100)}",
    '!монетка': lambda: f"🪙 Монетка: {'Орёл!' if random.choice([True, False]) else 'Решка!'}",
    '!кубик': lambda: f"🎶 Кубик: {random.randint(1, 6)}",
    '!помощь': lambda: (
        "📚 Доступные команды:\n"
        "  !время — текущее время\n"
        "  !дата — текущая дата\n"
        "  !часы — часы и минуты\n"
        "  !рандом — число от 1 до 100\n"
        "  !монетка — подбросить монетку\n"
        "  !кубик — бросить кубик (1-6)\n"
        "  !помощь — этот список\n"
        "  !цвет — случайный цвет\n"
        "  !факт — случайный факт"
    ),
    '!цвет': lambda: f"🎨 Цвет: #{random.randint(0, 0xFFFFFF):06X}",
    '!факт': get_random_fact,
}


class ChatService(pb2_grpc.ChatServiceServicer):
    def __init__(self):
        self.clients: dict[str, dict] = {}  # {username: {queue, emoji}}
        self.lock = threading.Lock()
        logging.info("💬 ChatService initialized with commands")

    def ChatStream(self, request_iterator, context):
        username = None
        emoji = "😀"  # Смайлик по умолчанию
        outgoing_queue: queue.Queue = queue.Queue()

        def read_incoming():
            nonlocal username, emoji
            try:
                for request in request_iterator:
                    if not username:
                        # Первое сообщение: регистрируем пользователя
                        username = request.username.strip() or f"User_{id(context) % 1000}"
                        emoji = request.emoji.strip() if request.emoji else "😀"
                        
                        with self.lock:
                            self.clients[username] = {"queue": outgoing_queue, "emoji": emoji}
                        
                        logging.info(f"🟢 Подключился: {username} {emoji}")
                        self._broadcast("SERVER", f"{username} {emoji} зашел в чат", exclude=username)
                    else:
                        # Проверяем на команды
                        text = request.text.strip()
                        if text.startswith('!'):
                            self._handle_command(text, username)
                        else:
                            logging.info(f"📨 {username} {emoji}: {text}")
                            self._broadcast(username, text, exclude=username, emoji=emoji)

            except grpc.RpcError as e:
                logging.info(f"🔌 Клиент {username} отключился")
            except Exception as e:
                logging.error(f"❌ Критическая ошибка у {username}: {e}")
            finally:
                if username:
                    logging.info(f"🔴 Отключился: {username}")
                    self._broadcast("SERVER", f"{username} {emoji} покинул чат", exclude=username)
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
            logging.warning(f"⚠️ Ошибка записи: {e.code()}")
        finally:
            reader_thread.join(timeout=2.0)

    def _handle_command(self, text: str, username: str):
        """Обрабатывает команды сервера"""
        command = text.lower().split()[0]  # Берём первое слово (команду)
        
        if command in SERVER_COMMANDS:
            try:
                result = SERVER_COMMANDS[command]()
                # Отправляем результат ВСЕМ (включая отправителя)
                self._broadcast("SERVER", f"{username}: {text}\n{result}", exclude=None)
            except Exception as e:
                self._broadcast("SERVER", f"⚠️ Ошибка команды: {e}", exclude=None)
        else:
            # Неизвестная команда — показываем подсказку
            self._broadcast("SERVER", f"❓ Неизвестная команда '{command}'. Введите !помощь", exclude=username)

    def _broadcast(self, sender: str, text: str, exclude: str = None, emoji: str = ""):
        """Отправляет сообщение всем клиентам, кроме exclude"""
        msg = pb2.ChatMessage(
            username=sender,
            text=text,
            emoji=emoji
        )
        with self.lock:
            for user_name, data in list(self.clients.items()):
                if exclude and user_name == exclude:
                    continue
                try:
                    data["queue"].put(msg)
                except Exception:
                    pass


def serve():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=100),
        options=[
            ('grpc.keepalive_time_ms', 60000),
            ('grpc.keepalive_timeout_ms', 10000),
            ('grpc.keepalive_permit_without_calls', 1),
            ('grpc.http2.max_pings_without_data', 2),  # Разрешаем 2 пинга без данных
            ('grpc.http2.min_ping_interval_without_data_ms', 30000), # Минимум 30 сек между пингами
        ]
    )
    pb2_grpc.add_ChatServiceServicer_to_server(ChatService(), server)
    port = os.getenv("PORT", "50051")
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    logging.info("✅ Сервер чата запущен на [::]:50051")
    logging.info("🌐 Доступен из локальной сети и Tailscale")
    logging.info("📚 Команды: !время, !дата, !рандом, !монетка, !кубик, !помощь, !цвет, !факт")

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