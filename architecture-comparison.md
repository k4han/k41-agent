# Quyet Dinh Kien Truc Module

## Tong quan

Ung dung dang trong giai doan phat trien, vi vay refactor nay chap nhan breaking changes cho import path noi bo. Muc tieu la giam layer bat buoc `domain/application/infrastructure/public.py` va dung package API ro rang hon:

```python
from agent.modules.agents import get_catalog_service
from agent.modules.workflows import get_workflow_graph
from agent.modules.providers import get_chat_model
```

Khong con dung import qua file `public.py` hoac import truc tiep vao cac layer cu.

## Trang thai sau refactor

```text
agent/
├── bootstrap/
├── delivery/http/
├── modules/
│   ├── admin_auth/
│   ├── agent_runtime/
│   ├── agents/
│   ├── channels/
│   │   ├── telegram/
│   │   └── discord/
│   ├── providers/
│   │   ├── google/
│   │   └── openai_compatible/
│   ├── scheduler/
│   ├── skills/
│   ├── tools/
│   │   ├── langchain/
│   │   └── runtime/
│   ├── users/
│   └── workflows/
│       ├── checkpoint/
│       ├── graphs/
│       ├── nodes/
│       └── state/
└── shared/
    ├── config/
    └── infrastructure/
```

## Nguyen tac

- Moi module expose public API qua `agent.modules.<module>` trong `__init__.py`.
- Code ben ngoai module khong import sau vao implementation cua module khac.
- Code trong cung module duoc import noi bo, vi du `agent.modules.workflows.graphs.router` co the import `agent.modules.workflows.registry`.
- `public.py` bi loai bo hoan toan; khong giu compatibility shim.
- Khong con layer folder bat buoc `domain`, `application`, `infrastructure` trong `agent/modules`.
- Module phuc tap van duoc chia subpackage theo domain thuc te, khong ep gom thanh mot file lon.

## Lua chon quan trong

- `workflows` duoc dua ve cau truc gan voi LangGraph domain: `graphs`, `nodes`, `checkpoint`, `state`, `registry.py`, `run_config.py`, `prompt_builders.py`.
- `channels` bo layer cu nhung giu `telegram/` va `discord/` vi logic moi channel da du phuc tap.
- `tools` giu nhom `langchain/` va `runtime/`; registry public nam o package API.
- `providers` van giu factory abstraction vi hien co nhieu provider implementation.
- `shared.config` van la package. Config da la unified service nen khong can gop thanh mot file.

## Kiem chung

Acceptance criteria cua refactor:

- Khong con import qua file `public.py` trong module.
- Khong con `agent/modules/*/{domain,application,infrastructure}`.
- Import boundary test bat loi code ngoai module import sau vao module khac.
- `uv run python -m compileall -q agent tests scripts` pass.
- Cac nhom pytest runtime, providers, workflows, tools, scheduler, API, dashboard pass.
