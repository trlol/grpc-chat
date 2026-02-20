#!/usr/bin/env python3
"""
Скрипт для генерации gRPC кода из .proto файлов
Запускай после любых изменений в proto/service.proto

Использование:
    python generate.py
"""

import subprocess
import sys
from pathlib import Path


def main():
    proto_dir = Path(__file__).parent / "proto"
    proto_file = proto_dir / "service.proto"
    output_dir = Path(__file__).parent  # Генерируем в корень!
    
    if not proto_file.exists():
        print(f"❌ Файл {proto_file} не найден!")
        sys.exit(1)
    
    print(f"🔨 Генерация кода из {proto_file}...")
    
    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"-I{proto_dir}",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        str(proto_file)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"❌ Ошибка генерации:")
        print(result.stderr)
        sys.exit(1)
    
    print("✅ Код успешно сгенерирован!")
    print("📁 Файлы:")
    print("   - service_pb2.py")
    print("   - service_pb2_grpc.py")
    print("\n🔁 Теперь можно запускать сервер или клиент!")


if __name__ == '__main__':
    main()