def main() -> None:
    print("Run the voice agent worker:")
    print("  uv run python -m voice_agent.agent dev")
    print("Run the web backend:")
    print("  uv run uvicorn voice_agent.web.app:create_app_from_env --factory --host 0.0.0.0 --port 8000")
