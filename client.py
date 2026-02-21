#!/usr/bin/env python3
"""
gRPC Chat Client
С выбором смайлика и поддержкой команд
Просто запусти: python client.py
"""
import grpc
import threading
import queue
import json
import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

import service_pb2 as pb2
import service_pb2_grpc as pb2_grpc


# === СПИСОК СМАЙЛИКОВ ДЛЯ ВЫБОРА ===
EMOJI_OPTIONS = [
    "🐵", "💩", "🐓", "😀", "😎", "🤖", "👻", "🤡", 
    "💀", "👹", "👽", 
    "🐱", "🐶", "🦊", "🐸", "🦄", "🐲", "🦋", "🐝",
    "🌟", "🔥", "💎", "🎯", "🎮", "🎲", "🎸", "🎺",
    "🍕", "🍔", "🌮", "🍩", "☕", "🍺", "🍷", "🧃",
    "🚀", "✈️", "🚗", "🚲", "⛵", "🛸", "🎈", "🎁",
    "❤️", "💛", "💚", "💙", "💜", "🧡", "💗", "💖",
]

# Пути к конфигу
DEFAULT_CONFIG_FILE = Path(__file__).parent / "config.json"
CONFIG_EXAMPLE = Path(__file__).parent / "config.json.example"

DEFAULT_CONFIG = {
    "server_ip": "localhost",
    "server_port": 50051,
    "username": "",
    "emoji": "",
    "auto_reconnect": True,
    "reconnect_delay": 2
}


def load_config(config_arg=None) -> tuple[dict, Path]:
    """Загружает конфиг из файла, указанного в аргументе или по умолчанию"""
    
    # Определяем какой файл использовать
    if config_arg:
        config_path = Path(config_arg)
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    print(f"📄 Загружен конфиг: {config_path.name}", flush=True)
                    return {**DEFAULT_CONFIG, **config}, config_path  # ← Возвращаем путь!
            except Exception as e:
                print(f"⚠️ Ошибка чтения {config_path}: {e}", flush=True)
        else:
            print(f"⚠️ Файл {config_path} не найден!", flush=True)
    
    # По умолчанию используем config.json
    config_path = DEFAULT_CONFIG_FILE
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {**DEFAULT_CONFIG, **config}, config_path  # ← Возвращаем путь!
        except json.JSONDecodeError as e:
            print(f"⚠️ Ошибка в config.json: {e}", flush=True)
    
    # Создаём из примера если нет
    if CONFIG_EXAMPLE.exists():
        import shutil
        shutil.copy2(CONFIG_EXAMPLE, DEFAULT_CONFIG_FILE)
        print(f"📝 Создан {DEFAULT_CONFIG_FILE.name} из примера", flush=True)
        print("✏️  Отредактируй перед запуском!", flush=True)
        sys.exit(0)
    
    return DEFAULT_CONFIG, DEFAULT_CONFIG_FILE


def select_emoji() -> str:
    """Позволяет пользователю выбрать смайлик"""
    print("\n=== ВЫБЕРИТЕ СМАЙЛИК ===", flush=True)
    
    # Показываем смайлики сеткой
    for i in range(0, len(EMOJI_OPTIONS), 8):
        row = EMOJI_OPTIONS[i:i+8]
        print("  " + "  ".join(f"{j} {emoji}" for j, emoji in enumerate(row, start=i)), flush=True)
    
    print(f"\nВведите номер смайлика (0-{len(EMOJI_OPTIONS)-1}) или свой: ", end='', flush=True)
    
    try:
        choice = input().strip()
        if choice.isdigit() and 0 <= int(choice) < len(EMOJI_OPTIONS):
            return EMOJI_OPTIONS[int(choice)]
        elif choice and len(choice) <= 4:  # Свой смайлик (1-4 символа)
            return choice
        else:
            return "😀"  # По умолчанию
    except:
        return "😀"


class ChatClient:
    def __init__(self, config: dict):
        self.config = config
        self.server_addr = f"{config['server_ip']}:{config['server_port']}"
        self.username = config['username']
        self.emoji = config.get('emoji', '😀')
        self.channel = None
        self.stub = None
        self.outgoing_queue: queue.Queue = queue.Queue()
        self.running = True
        self.input_lock = threading.Lock()
        self.connected = threading.Event()

    def connect(self, timeout: int = 10) -> bool:
        print(f"🔌 Подключение к {self.server_addr}...", flush=True)
        
        for attempt in range(5):
            try:
                self.channel = grpc.insecure_channel(
                    self.server_addr,
                    options=[
                        ('grpc.keepalive_time_ms', 60000),
                        ('grpc.keepalive_timeout_ms', 10000),
                    ]
                )
                self.stub = pb2_grpc.ChatServiceStub(self.channel)
                grpc.channel_ready_future(self.channel).result(timeout=timeout)
                print(f"✅ Подключено к {self.server_addr}", flush=True)
                return True
            except Exception as e:
                delay = self.config['reconnect_delay'] * (2 ** attempt)
                print(f"⚠️ Ошибка подключения (попытка {attempt+1}/5): {type(e).__name__}", flush=True)
                if not self.config['auto_reconnect']:
                    return False
                time.sleep(delay)
        
        print("❌ Не удалось подключиться", flush=True)
        return False

    def generate_outgoing(self):
        """Генератор исходящих сообщений"""
        # Первое сообщение — регистрация с именем и смайликом
        yield pb2.ChatMessage(username=self.username, text="", emoji=self.emoji)
        self.connected.set()
        
        while self.running:
            try:
                msg = self.outgoing_queue.get(timeout=0.5)
                if msg is None:
                    break
                yield msg
            except queue.Empty:
                continue

    def receive_loop(self, response_iterator):
        """Поток получения сообщений"""
        try:
            for response in response_iterator:
                with self.input_lock:
                    # Очищаем текущую строку
                    print('\r' + ' ' * 80 + '\r', end='', flush=True)
                    
                    if response.username == "SERVER":
                        # Сообщения от сервера (команды, системные)
                        print(f"🔔 {response.text}")
                    else:
                        # Сообщения от пользователей
                        user_emoji = response.emoji if response.emoji else "😀"
                        print(f"{user_emoji} {response.username}: {response.text}")
                    
                    # Возвращаем приглашение для ввода
                    print(f"{self.emoji} Вы: ", end='', flush=True)
                    
        except grpc.RpcError as e:
            print(f"\n❌ Соединение разорвано: {e.code()}", flush=True)
        finally:
            self.running = False

    def input_loop(self):
        """Поток ввода с клавиатуры"""
        print("=== Чат запущен! Вводите сообщения (exit/quit для выхода) ===", flush=True)
        print("💡 Введите !помощь для списка команд", flush=True)
        
        self.connected.wait()
        print(f"{self.emoji} Вы: ", end='', flush=True)
        
        while self.running:
            try:
                text = input()
                if not text:
                    print(f"{self.emoji} Вы: ", end='', flush=True)
                    continue
                
                if text.lower() in ['exit', 'quit', 'пока', '/quit']:
                    print("👋 Выход из чата...")
                    self.outgoing_queue.put(None)
                    break
                
                msg = pb2.ChatMessage(username=self.username, text=text, emoji=self.emoji)
                self.outgoing_queue.put(msg)
                print(f"{self.emoji} Вы: ", end='', flush=True)
                
            except (EOFError, KeyboardInterrupt):
                print("\n👋 Выход по прерыванию...")
                break
        
        self.outgoing_queue.put(None)
        self.running = False

    def start(self):
        if not self.connect():
            return 1
        
        response_iterator = self.stub.ChatStream(self.generate_outgoing())
        receiver = threading.Thread(target=self.receive_loop, args=(response_iterator,), daemon=True)
        receiver.start()
        self.input_loop()
        receiver.join(timeout=5)
        
        if self.channel:
            self.channel.close()
        
        return 0


def main():
    parser = argparse.ArgumentParser(description='gRPC Chat Client')
    parser.add_argument('--config', '-c', type=str, default=None,
                        help='Путь к файлу конфигурации (по умолчанию: config.json)')
    args = parser.parse_args()
    
    # ← Теперь получаем и конфиг, и путь к файлу
    config, config_path = load_config(args.config)
    
    username = config['username'].strip()
    if not username:
        username = input("Введите ваше имя: ").strip() or f"User_{os.getpid()}"
    
    emoji = config.get('emoji', '').strip()
    if not emoji:
        emoji = select_emoji()
        config['emoji'] = emoji

    if not config['username']:
        config['username'] = username
    
    # ← Сохраняем в ТОТ ЖЕ файл, из которого загрузили!
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"💾 Конфиг сохранён в {config_path.name}", flush=True)
    except Exception as e:
        print(f"⚠️ Не удалось сохранить конфиг: {e}", flush=True)
    
    config['username'] = username
    client = ChatClient(config)
    exit_code = client.start()
    sys.exit(exit_code)


if __name__ == '__main__':
    main()