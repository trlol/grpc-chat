#!/usr/bin/env python3
"""
gRPC Chat Client
Автоматически читает настройки из config.json

Просто запусти: python client.py
"""

import grpc
import threading
import queue
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Импорты из корня проекта (не из app!)
import service_pb2 as pb2
import service_pb2_grpc as pb2_grpc


CONFIG_FILE = Path(__file__).parent / "config.json"
CONFIG_EXAMPLE = Path(__file__).parent / "config.json.example"

DEFAULT_CONFIG = {
    "server_ip": "localhost",
    "server_port": 50051,
    "username": "",
    "auto_reconnect": True,
    "reconnect_delay": 2
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {**DEFAULT_CONFIG, **config}
        except json.JSONDecodeError as e:
            print(f"⚠️ Ошибка в config.json: {e}", flush=True)
            print("📋 Используется конфигурация по умолчанию", flush=True)
    
    if CONFIG_EXAMPLE.exists():
        import shutil
        shutil.copy2(CONFIG_EXAMPLE, CONFIG_FILE)
        print(f"📝 Создан {CONFIG_FILE.name} из примера", flush=True)
        print("✏️  Отредактируй server_ip и username перед запуском!", flush=True)
        print("🔁 Перезапусти клиент после редактирования\n", flush=True)
        sys.exit(0)
    else:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        print(f"📝 Создан {CONFIG_FILE.name} с настройками по умолчанию", flush=True)
        print("✏️  Отредактируй server_ip и username перед запуском!", flush=True)
        print("🔁 Перезапусти клиент после редактирования\n", flush=True)
        sys.exit(0)
    
    return DEFAULT_CONFIG


class ChatClient:
    def __init__(self, config: dict):
        self.config = config
        self.server_addr = f"{config['server_ip']}:{config['server_port']}"
        self.username = config['username']
        self.channel = None
        self.stub = None
        self.outgoing_queue: queue.Queue = queue.Queue()
        self.running = True

    def connect(self, timeout: int = 10) -> bool:
        print(f"🔌 Подключение к {self.server_addr}...", flush=True)
        
        for attempt in range(5):
            try:
                self.channel = grpc.insecure_channel(
                    self.server_addr,
                    options=[
                        ('grpc.keepalive_time_ms', 10000),
                        ('grpc.keepalive_timeout_ms', 5000),
                        ('grpc.http2.max_pings_without_data', 0),
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
                timestamp = datetime.fromtimestamp(response.timestamp / 1000).strftime('%H:%M:%S')
                prefix = "🟢" if response.username == self.username else "👤"
                print(f"\r{prefix} [{timestamp}] {response.username}: {response.text}", flush=True)
                print("Вы: ", end='', flush=True)
        except grpc.RpcError as e:
            print(f"\n❌ Соединение разорвано: {e.code()}", flush=True)
        finally:
            self.running = False

    def input_loop(self):
        print("=== Чат запущен! Вводите сообщения (exit/quit для выхода) ===", flush=True)
        print("Вы: ", end='', flush=True)
        
        while self.running:
            try:
                text = input()
                if not text:
                    print("Вы: ", end='', flush=True)
                    continue
                if text.lower() in ['exit', 'quit', 'пока', '/quit']:
                    self.outgoing_queue.put(
                        pb2.ChatMessage(username=self.username, text=text, timestamp=int(time.time()*1000))
                    )
                    break
                self.outgoing_queue.put(
                    pb2.ChatMessage(username=self.username, text=text, timestamp=int(time.time()*1000))
                )
            except (EOFError, KeyboardInterrupt):
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
        print("👋 Сеанс завершен", flush=True)
        return 0


def main():
    config = load_config()
    
    username = config['username'].strip()
    if not username:
        username = input("Введите ваше имя: ").strip() or f"User_{os.getpid()}"
        if not config['username']:
            config['username'] = username
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            except:
                pass
    
    config['username'] = username
    
    client = ChatClient(config)
    exit_code = client.start()
    sys.exit(exit_code)


if __name__ == '__main__':
    main()