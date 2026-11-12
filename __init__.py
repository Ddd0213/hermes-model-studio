from pathlib import Path
import importlib.util
import sys

def register(ctx):
    root = Path(__file__).resolve().parent / "model_studio"
    if "model_studio" not in sys.modules:
        spec = importlib.util.spec_from_file_location("model_studio", root / "__init__.py", submodule_search_locations=[str(root)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["model_studio"] = mod
        spec.loader.exec_module(mod)
    from model_studio.runtime import middleware
    ctx.register_middleware("llm_request", middleware)
