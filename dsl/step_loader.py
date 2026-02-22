import logging  
def load_steps(path: str):
    """Reads flow file and returns executable steps."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            step = line.strip()

            # skip empty lines and comments
            if not step or step.startswith("#"):
                continue

            yield step
