from . import Config
from . import ArchivingOrchestrator

def main():
    config = Config()
    config.parse()
    orchestrator = ArchivingOrchestrator(config)
    for _ in orchestrator.feed():
        pass


if __name__ == "__main__":
    main()
