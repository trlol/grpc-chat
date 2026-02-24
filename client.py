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

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_formatted_text


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
    "server_ip": "100.73.134.53",
    "server_port": 50051,
    "username": "",
    "emoji": "",
    "auto_reconnect": True,
    "reconnect_delay": 2
}


def load_config(config_arg=None) -> tuple[dict, Path]:
    if config_arg:
        config_path = Path(config_arg)
    else:
        config_path = DEFAULT_CONFIG_FILE

    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return {**DEFAULT_CONFIG, **config}, config_path

    # если файла нет — создаём пустой
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)

    print(f"📝 Создан новый {config_path.name}")
    return DEFAULT_CONFIG.copy(), config_path
    
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
        self.session = PromptSession()

    def connect(self, timeout: int = 30) -> bool:
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
        try:
            for response in response_iterator:
                if response.username == "SERVER":
                    print_formatted_text(f"🔔 {response.text}")
                else:
                    user_emoji = response.emoji if response.emoji else "😀"
                    print_formatted_text(f"{user_emoji} {response.username}: {response.text}")

        except grpc.RpcError as e:
            print_formatted_text(f"\n❌ Соединение разорвано: {e.code()}")
        finally:
            self.running = False

    def input_loop(self):
        print("=== Чат запущен! Вводите сообщения (exit/quit для выхода) ===")
        print("💡 Введите !помощь для списка команд")

        self.connected.wait()

        with patch_stdout():
            while self.running:
                try:
                    text = self.session.prompt(f"{self.emoji} Вы: ")

                    if not text:
                        continue

                    if text.lower() in ['exit', 'quit', 'пока', '/quit']:
                        print("👋 Выход из чата...")
                        self.outgoing_queue.put(None)
                        break

                    msg = pb2.ChatMessage(
                        username=self.username,
                        text=text,
                        emoji=self.emoji
                    )
                    self.outgoing_queue.put(msg)

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
    parser.add_argument('--config', '-c', type=str, default=None)
    args = parser.parse_args()

    config, config_path = load_config(args.config)

    first_run = not config.get("username") or not config.get("emoji")

    if first_run:
        print("🚀 Первый запуск! Давайте настроим чат.\n")

        username = input("Введите ваше имя: ").strip()
        while not username:
            username = input("Имя не может быть пустым. Введите ваше имя: ").strip()

        emoji = select_emoji()

        config["username"] = username
        config["emoji"] = emoji

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        print(f"\n💾 Настройки сохранены в {config_path.name}\n")

    client = ChatClient(config)
    exit_code = client.start()
    sys.exit(exit_code)


if __name__ == '__main__':
    main()