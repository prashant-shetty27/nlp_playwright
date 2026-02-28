import logging          

from playwright.sync_api import sync_playwright
from executions.execution_session import ExecutionSession
from executions import Executor
from executions.ActionService import ActionService
from dsl.step_loader import load_steps
from dsl.parser import parse_step   # assuming you already created this

def run_flow(path, logger):

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        session = ExecutionSession(page, logger)
        actions = ActionService(session)
        executor = Executor(actions, logger)

        try:
            for step in load_steps(path):
                command = parse_step(step)
                executor.execute(command)

        finally:
            logger.info("🧹 Cleaning up browser session")
            context.close()
            browser.close()
