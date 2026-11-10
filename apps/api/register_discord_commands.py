import os
import sys
from pathlib import Path

import httpx


def main():
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))
    application_id = os.getenv("DISCORD_APPLICATION_ID", "").strip()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not application_id or not token:
        print("Set DISCORD_APPLICATION_ID and DISCORD_BOT_TOKEN first")
        return 1
    base = f"https://discord.com/api/v10/applications/{application_id}/commands"
    commands = [
        {
            "name": "link",
            "description": "Connect this Discord channel to HackTrack",
            "options": [{"type": 3, "name": "token", "description": "One-time code from HackTrack Settings", "required": True, "max_length": 64}],
        },
        {
            "name": "track",
            "description": "Add a hackathon link to HackTrack",
            "options": [{"type": 3, "name": "url", "description": "Hackathon page URL", "required": True, "max_length": 2048}],
        },
        {"type": 3, "name": "Track link"},
    ]
    try:
        with httpx.Client(timeout=15.0, headers={"Authorization": f"Bot {token}"}) as client:
            for command in commands:
                response = client.post(base, json=command)
                response.raise_for_status()
        print("Registered /link, /track, and the Track link message action")
        return 0
    except httpx.HTTPError as error:
        print(f"Could not register Discord commands: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
