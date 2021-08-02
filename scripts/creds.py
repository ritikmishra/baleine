import os
import getpass

GAME_ID = os.getenv("ANACREON_GAME_ID", "8JNJ7FNZ")

if (_username := os.getenv("ANACREON_USERNAME")) is not None:
    USERNAME = _username
else:
    USERNAME = input("Multiverse username: ")

if (_multiverse_password := os.getenv("ANACREON_PASSWORD")) is not None:
    PASSWORD = _multiverse_password
else:
    import getpass
    PASSWORD = getpass.getpass("Multiverse password (text will be hidden): ")
