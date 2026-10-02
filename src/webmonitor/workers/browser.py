"""Sandboxed browser collection process."""
from webmonitor.workers.collection import main as collection_main

async def main():
    await collection_main("browser")
