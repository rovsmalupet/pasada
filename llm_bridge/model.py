"""Loading a model and asking it, as evaluated (notebook 1.3): 4-bit, the whole model on the GPU."""

import gc
import time

from . import config as C

model = tok = CURRENT = None


def wait_for_gpu(name, need_gb):
    import torch
    waited = 0
    while True:
        free_gb = torch.cuda.mem_get_info()[0] / 1e9
        if free_gb >= need_gb:
            return free_gb
        if waited >= C.GPU_WAIT_MINUTES:
            raise SystemExit(f"{name} needs about {need_gb} GB of free GPU memory; only {free_gb:.1f} GB was free "
                             f"after {waited} min (the GPU is shared). Try again later.")
        print(f"{name}: only {free_gb:.1f} GB of GPU memory free, needs {need_gb}; waiting 5 min...", flush=True)
        time.sleep(300)
        waited += 5


def load(name):
    """Load `name` in 4-bit: from its saved copy in ~/thesis_llm/models if there is one, else from
    HuggingFace (the same settings either way)."""
    global model, tok, CURRENT
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    if CURRENT == name:
        return
    model = tok = CURRENT = None
    gc.collect()
    torch.cuda.empty_cache()
    info = C.MODELS[name]
    free_gb = wait_for_gpu(name, info["min_gpu_gb"])
    on_gpu = {"": 0}
    if (info["saved_copy"] / "config.json").is_file():
        print(f"loading {name} from {info['saved_copy']} ({free_gb:.1f} GB GPU free)...", flush=True)
        tok = AutoTokenizer.from_pretrained(info["saved_copy"])
        model = AutoModelForCausalLM.from_pretrained(info["saved_copy"], device_map=on_gpu)
    else:
        print(f"loading {name} from HuggingFace in 4-bit ({free_gb:.1f} GB GPU free)...", flush=True)
        tok = AutoTokenizer.from_pretrained(info["repo"])
        model = AutoModelForCausalLM.from_pretrained(
            info["repo"], device_map=on_gpu,
            quantization_config=BitsAndBytesConfig(load_in_4bit=True,
                                                   bnb_4bit_compute_dtype=getattr(torch, info["compute_dtype"])))
    CURRENT = name
    print("loaded:", name, "|", info["repo"], flush=True)


def chat_text(system, user):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    extra = {"enable_thinking": False} if C.MODELS[CURRENT]["thinking_off"] else {}
    return tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, **extra)


def ask(system, user, max_new_tokens=C.MAX_NEW_TOKENS):
    """One prompt, as in the evaluation notebook."""
    return ask_batch(system, [user], max_new_tokens)[0]


def ask_batch(system, users, max_new_tokens=C.MAX_NEW_TOKENS):
    """Several prompts in one generate call: padded on the left, with an attention mask so the
    padding is ignored. Each answer is still sampled on its own, with the same settings."""
    import torch
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "left"
    texts = [chat_text(system, u) for u in users]
    inputs = tok(texts, return_tensors="pt", padding=True).to(model.device)
    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=max_new_tokens, pad_token_id=tok.pad_token_id, **C.GEN)
    start = inputs["input_ids"].shape[1]
    return [tok.decode(row[start:], skip_special_tokens=True) for row in output]
