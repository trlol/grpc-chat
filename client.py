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
import argparse
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


def load_config(config_arg=None) -> dict:
    """Загружает конфиг из файла, указанного в аргументе или по умолчанию"""
    
    # Если передан аргумент --config, используем его
    if config_arg:
        config_path = Path(config_arg)
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    print(f"📄 Загружен конфиг: {config_path.name}", flush=True)
                    return {**DEFAULT_CONFIG, **config}
            except Exception as e:
                print(f"⚠️ Ошибка чтения {config_path}: {e}", flush=True)
        else:
            print(f"⚠️ Файл {config_path} не найден!", flush=True)
    
    # По умолчанию используем config.json
    config_path = CONFIG_FILE
    
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {**DEFAULT_CONFIG, **config}
        except json.JSONDecodeError as e:
            print(f"⚠️ Ошибка в config.json: {e}", flush=True)
    
    # Создаём из примера если нет
    if CONFIG_EXAMPLE.exists():
        import shutil
        shutil.copy2(CONFIG_EXAMPLE, CONFIG_FILE)
        print(f"📝 Создан {CONFIG_FILE.name} из примера", flush=True)
        print("✏️  Отредактируй перед запуском!", flush=True)
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
        self.input_lock = threading.Lock()
        self.connected = threading.Event()  # Добавляем событие для синхронизации

    def connect(self, timeout: int = 10) -> bool:
        print(f"🔌 Подключение к {self.server_addr}...", flush=True)
        
        for attempt in range(5):
            try:
                self.channel = grpc.insecure_channel(
                    self.server_addr,
                    options=[
                        ('grpc.keepalive_time_ms', 60000),
                        ('grpc.keepalive_timeout_ms', 10000),
                        ('grpc.keepalive_permit_without_calls', 1),
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
        """Генератор исходящих сообщений"""
        # Сразу отправляем пустое сообщение с ником для регистрации на сервере
        yield pb2.ChatMessage(username=self.username, text="")
        
        # Сигнализируем что регистрация прошла
        self.connected.set()
        
        # Отправляем остальные сообщения
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
                # НЕ показываем свои сообщения - они уже показаны как "Вы:"
                if response.username == self.username:
                    continue
                    
                with self.input_lock:
                    # Очищаем текущую строку "Вы: "
                    print('\r' + ' ' * 80 + '\r', end='', flush=True)
                    
                    # Выводим полученное сообщение
                    if response.username == "SERVER":
                        print(f"🔔 {response.text}")
                    else:
                        print(f"👤 {response.username}: {response.text}")
                    
                    # Возвращаем приглашение для ввода
                    print("Вы: ", end='', flush=True)
                    
        except grpc.RpcError as e:
            print(f"\n❌ Соединение разорвано: {e.code()}", flush=True)
        finally:
            self.running = False

    def input_loop(self):
        print("=== Чат запущен! Вводите сообщения (exit/quit для выхода) ===", flush=True)
        
        # Ждем подключения к серверу
        self.connected.wait()
        
        print("Вы: ", end='', flush=True)
        
        while self.running:
            try:
                text = input()
                if not text:
                    print("Вы: ", end='', flush=True)
                    continue
                
                # Проверка на выход
                if text.lower() in ['exit', 'quit', 'пока', '/quit']:
                    print("👋 Выход из чата...")
                    self.outgoing_queue.put(None)
                    break
                
                msg = pb2.ChatMessage(username=self.username, text=text)
                
                # Отправляем сообщение
                self.outgoing_queue.put(msg)
                
                # Возвращаем приглашение для ввода
                print("Вы: ", end='', flush=True)
                
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
    
    config = load_config(args.config)
    
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