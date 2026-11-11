"""Settings of the LLM bridge: where things are, the models, and the generation settings.

The generation settings and the model list are the evaluation notebook's
(PASADA-llm/notebooks/commuter_prompts_v4_server.ipynb, cell 1.2), so the simulation runs the
models exactly as they were evaluated. Paths can be changed with environment variables.
"""

import os
from pathlib import Path

HOME = Path.home()
# the LLM team's files on the server (models, knowledge base)
THESIS_LLM = Path(os.environ.get("THESIS_LLM", HOME / "thesis_llm"))
MODELS_DIR = THESIS_LLM / "models"
KB_FILE = Path(os.environ.get("KB_FILE", THESIS_LLM / "inputs" / "knowledge_base_factors_v1.csv"))
# the simulation folder whose demand/ the bridge reads and writes (Rovick's starosa_bayan)
ABM_DIR = Path(os.environ.get("ABM_DIR", Path(__file__).resolve().parent.parent / "starosa_bayan"))

MODELS = {
    "llama":   {"repo": "meta-llama/Llama-3.1-8B-Instruct", "saved_copy": MODELS_DIR / "llama31-8b-4bit",
                "compute_dtype": "float16", "thinking_off": False, "min_gpu_gb": 8},
    "qwen":    {"repo": "Qwen/Qwen3-8B", "saved_copy": MODELS_DIR / "qwen3-8b-4bit",
                "compute_dtype": "float16", "thinking_off": True, "min_gpu_gb": 8},
    "gemma":   {"repo": "google/gemma-3-12b-it", "saved_copy": MODELS_DIR / "gemma3-12b-4bit",
                "compute_dtype": "bfloat16", "thinking_off": False, "min_gpu_gb": 12},
    "qwen32":  {"repo": "Qwen/Qwen3-32B", "saved_copy": MODELS_DIR / "qwen3-32b-4bit",
                "compute_dtype": "float16", "thinking_off": True, "min_gpu_gb": 21},
    "sealion": {"repo": "aisingapore/Qwen-SEA-LION-v4-32B-IT", "saved_copy": MODELS_DIR / "sealion-v4-32b-4bit",
                "compute_dtype": "float16", "thinking_off": True, "min_gpu_gb": 21},
}
DEFAULT_MODEL = "llama"            # the baseline (manuscript 4.3.8); change with --model

GEN = {"do_sample": True, "temperature": 0.6, "top_p": 0.9, "top_k": 50, "repetition_penalty": 1.0}
MAX_NEW_TOKENS = 900               # as evaluated; mode and reflection answers for many trips get more
MAX_NEW_TOKENS_LONG = 1500
MAX_TRIES = 3                      # manuscript 4.3.9.1: a failed answer is regenerated, up to 3 tries
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", 8))   # agents answered together in one generate call
GPU_WAIT_MINUTES = 60              # wait this long for free GPU memory (the GPU is shared)

# v0.3 = the evaluated prompts (2026-11-06). "-abm1" = the small changes the simulation needs,
# "-abm2" = plus the habits clarifications (exact location ids, different places, a chain example),
# "-abm3" = home shown as HOME in every prompt; habits asks for round trips from home,
# "-abm4" = the schedule may also use listed places besides the frequent ones,
# "-abm5" = places named by category only (no ids); the nearest place of the category is used,
# "-abm6" = no distances or reachability in any prompt: every kind of place is offered (2026-11-11),
# "-abm7" = (no prompt change) a kind resolves to the place nearest to where the trip starts,
# "-abm8" = habits: outings on one day must not overlap; different departure times (2026-11-11),
# each marked "INTEGRATION" in prompts.py (2026-11-10).
PROMPT_VERSION = "v0.3-abm8"

KB_TOP_K = 8
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# mode names: the prompts (as evaluated) <-> the simulation (Rovick's demand files)
TO_ABM_MODE = {"jeepney": "jeepney", "walking": "walk", "bicycle": "bike", "private car": "car"}
TO_PROMPT_MODE = {v: k for k, v in TO_ABM_MODE.items()}

# the simulation's rules (starosa_bayan/settings.py), repeated here so the bridge does not need SUMO
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_NAMES = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday",
             "Sat": "Saturday", "Sun": "Sunday"}
def _simulation_settings():
    """starosa_bayan/settings.py (it imports only the standard library), so the bridge and the
    simulation share one list of activities and the place categories they can happen at."""
    import importlib.util
    path = ABM_DIR / "settings.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("abm_settings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_S = _simulation_settings()
ACTIVITIES = dict(_S.ACTIVITIES) if _S else {   # activity: the place categories it may happen at
    "work": ("workplace", "school", "market", "mall", "terminal", "shop", "food", "recreation", "services",
             "healthcare", "worship"),
    "school": ("school",),
    "shopping": ("market", "mall", "shop"),
    "leisure": ("mall", "recreation", "food"),
    "personal": ("healthcare", "services", "worship"),
    "out_of_area": ("terminal",),
}
LEVELS = ("Low", "Moderate", "High")
SENSITIVITIES = ("safety", "cost", "comfort", "accessibility", "availability", "efficiency", "reliability")
CAR_DRIVER_MIN_AGE = 20
WEIGHT_SUM_TOLERANCE = 0.01
MAX_WEIGHT_CHANGE = 0.2
VEHICLE_MODES = ("bike", "car")

# the persona's two active-mobility fields in personas.json. The evaluated prompt writes
# walking/cycling *sensitivity* (High = it discourages them); llm_tasks.py was changed to the
# same names on the llm-integration branch.
WALKING_FIELD = "walking_sensitivity"
CYCLING_FIELD = "cycling_sensitivity"
