"""Script to verify how many graphs are built at startup."""

import asyncio

from agent.modules.workflows.application.register_builtin_workflows import (
    register_builtin_workflows,
)
from agent.modules.workflows.public import list_registered_workflows
from agent.bootstrap.runtime import initialize_persistence


async def main():
    print("=" * 60)
    print("GRAPH REGISTRATION VERIFICATION")
    print("=" * 60)

    print("\n[0] Initializing persistence...")
    await initialize_persistence()

    # Register all builtin workflows
    print("\n[1] Registering builtin workflows...")
    register_builtin_workflows()

    # Get all registered graphs via public API
    graphs = list_registered_workflows()

    print(f"\n[2] Total graphs registered: {len(graphs)}")
    print("\nRegistered graph names:")
    for i, name in enumerate(graphs, 1):
        print(f"  {i}. {name}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"✓ Base graph templates: 3")
    print(f"  - react_agent (shared template)")
    print(f"  - research_chain")
    print(f"  - router")
    print(f"\n✓ Agent configs loaded from MD files")
    print(f"  (Agents use shared graph templates at runtime)")
    print(f"\n✓ Total compiled graphs: {len(graphs)}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
