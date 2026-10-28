import argparse
from . import agent, config, ui

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", type=str, help="Start in specific persona")
    parser.add_argument("--text", action="store_true", help="Use text mode")
    parser.add_argument("--list", action="store_true", help="List personas")
    parser.add_argument("--web", action="store_true", help="Run the Web UI server")
    args = parser.parse_args()

    if args.list:
        from .personas import load_personas
        print("Available personas:", ", ".join(load_personas().keys()))
        return

    if args.web:
        from . import web
        import uvicorn
        uvicorn.run(app=web.app, host=config.cfg.web_host, port=config.cfg.web_port)
        return

    # Standard terminal agent
    ui_class = ui.TextUI if args.text else ui.VoiceUI
    ui_instance = ui_class()
    
    session = agent.Session(ui_instance, persona=args.persona)
    
    if args.text:
        print("VoiceAgent (Text Mode). Type 'goodbye' to exit.")
    else:
        print(f"VoiceAgent started in {session.current} mode.")

    while True:
        try:
            text = ui_instance.listen()
            if not text: continue
            if text.lower() in ("goodbye", "exit", "quit"):
                session.finalize()
                break
            session.handle(text)
        except KeyboardInterrupt:
            session.finalize(propose=False)
            break

if __name__ == "__main__":
    main()
