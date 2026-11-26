"""Manual real-engine smoke test with original synthetic weights and no downloads."""

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


def make_fixture(path):
    import gguf
    import numpy as np

    # A tiny Llama architecture with a known Q -> O -> K -> EOS transition.
    tokens = ["<unk>", "<s>", "</s>", "▁", "Q", "O", "K"]
    tokens += [f"<0x{i:02X}>" for i in range(256)]
    w = gguf.GGUFWriter(path, "llama")
    w.add_name("original-transition-fixture")
    w.add_context_length(512)
    w.add_embedding_length(32)
    w.add_block_count(1)
    w.add_feed_forward_length(32)
    w.add_head_count(2)
    w.add_layer_norm_rms_eps(1e-5)
    w.add_tokenizer_model("llama")
    w.add_token_list(tokens)
    w.add_token_scores([0.0] * len(tokens))
    w.add_token_types([2, 3, 3, 1, 1, 1, 1] + [6] * 256)
    w.add_bos_token_id(1)
    w.add_eos_token_id(2)
    embed = np.zeros((len(tokens), 32), dtype=np.float32)
    out = embed.copy()
    for idx, target in [(4, 5), (5, 6), (6, 2)]:
        embed[idx, idx] = 1
        out[target, idx] = 20
    w.add_tensor("token_embd.weight", embed)
    w.add_tensor("output.weight", out)
    for key in ["output_norm", "blk.0.attn_norm", "blk.0.ffn_norm"]:
        w.add_tensor(key + ".weight", np.ones(32, dtype=np.float32))
    for key in [
        "attn_q",
        "attn_k",
        "attn_v",
        "attn_output",
        "ffn_gate",
        "ffn_up",
        "ffn_down",
    ]:
        w.add_tensor("blk.0." + key + ".weight", np.zeros((32, 32), dtype=np.float32))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("llama_server", type=Path)
    server = parser.parse_args().llama_server.resolve(strict=True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_fixture(root / "model.gguf")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (root / "server.log").open("w") as log:
            process = subprocess.Popen(
                [
                    str(server),
                    "-m",
                    str(root / "model.gguf"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "-c",
                    "512",
                    "-t",
                    "1",
                    "-ngl",
                    "0",
                ],
                stdout=log,
                stderr=log,
            )

        def cli(command, data):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "raya",
                    "--profile",
                    str(root / "profile"),
                    command,
                    "-",
                ],
                input=json.dumps(data),
                capture_output=True,
                text=True,
                check=True,
                timeout=70,
            )
            return json.loads(result.stdout)

        try:
            cli("set", {"field": "provider_port", "value": port})
            cli("set", {"field": "provider", "value": "local-llama"})
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError((root / "server.log").read_text()[-3000:])
                if cli("provider-status", {})["state"] == "ready":
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("server readiness timed out")
            req = cli("generate", {"prompt": "Q"})
            result = cli("approve", {"id": req["id"], "digest": req["digest"]})
            if result.get("result") != "OK" or result["state"] != "completed":
                raise RuntimeError(str(result))
            print("Real llama-server CPU completion: Q -> OK")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
