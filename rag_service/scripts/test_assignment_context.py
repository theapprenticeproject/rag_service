import asyncio

from rag_service.core.assignment_context_manager import AssignmentContextManager


async def main():
    manager = AssignmentContextManager()
    a = await manager.get_assignment_context(
        assignment_id="Build Your First Animation 🐱-Basic",
        student_id="ST00483909",
    )
    print(a)


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
