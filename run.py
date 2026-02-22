import sys
from engine import run_steps

if __name__ == "__main__":
    # Accept flow file as argument, default to steps.flow
    flow_file = sys.argv[1] if len(sys.argv) > 1 else "steps.flow"
    run_steps(flow_file)
