"""Provider wire contracts and validation of model-produced data."""

import json
import math

from .core import encode, identifier, require

# All but Anthropic offer the documented OpenAI-compatible chat endpoint.
HOSTS = {
    "openai": ("api.openai.com", "/v1/chat/completions"),
    "anthropic": ("api.anthropic.com", "/v1/messages"),
    "gemini": ("generativelanguage.googleapis.com", "/v1beta/openai/chat/completions"),
    "xai": ("api.x.ai", "/v1/chat/completions"),
    "deepseek": ("api.deepseek.com", "/chat/completions"),
    "groq": ("api.groq.com", "/openai/v1/chat/completions"),
    "openrouter": ("openrouter.ai", "/api/v1/chat/completions"),
    "chatgpt": ("api.openai.com", "/v1/responses"),
    "omniroute": ("127.0.0.1", "/v1/chat/completions"),
    "sambanova": ("api.sambanova.ai", "/v1/chat/completions"),
    "nim": ("integrate.api.nvidia.com", "/v1/chat/completions"),
    "cloudflare": (
        "api.cloudflare.com",
        "/client/v4/accounts/{account}/ai/v1/chat/completions",
    ),
    "mistral": ("api.mistral.ai", "/v1/chat/completions"),
    "cohere": ("api.cohere.ai", "/compatibility/v1/chat/completions"),
    "ollama": ("127.0.0.1", "/v1/chat/completions"),
    "llama.cpp": ("127.0.0.1", "/v1/chat/completions"),
}
LOCAL = {"ollama", "llama.cpp"}
TYPES = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def schema(spec, depth=0):
    require(depth <= 8 and isinstance(spec, dict), "invalid schema")
    kind = spec.get("type")
    require(
        isinstance(kind, str) and kind in {*TYPES, "number"}, "unsupported schema type"
    )
    allowed = {"type", "description", "enum"}
    if kind == "object":
        allowed.update(("properties", "required", "additionalProperties"))
        props = spec.get("properties", {})
        required = spec.get("required", [])
        require(
            isinstance(props, dict)
            and isinstance(required, list)
            and all(isinstance(k, str) and k in props for k in required),
            "invalid object schema",
        )
        require(
            type(spec.get("additionalProperties", True)) is bool,
            "invalid additionalProperties",
        )
        for value in props.values():
            schema(value, depth + 1)
    if kind == "array":
        allowed.add("items")
        schema(spec.get("items"), depth + 1)
    require(isinstance(spec.get("description", ""), str), "invalid schema description")
    require(set(spec) <= allowed, "unsupported schema keyword")
    require(
        "enum" not in spec or isinstance(spec["enum"], list) and spec["enum"],
        "invalid enum",
    )


def validate(value, spec):
    kind = spec["type"]
    if kind == "number":
        require(
            type(value) in (int, float) and math.isfinite(value),
            "expected finite number",
        )
    else:
        require(type(value) is TYPES[kind], "output does not match schema type")
    if "enum" in spec:
        require(
            any(encode(value) == encode(v) for v in spec["enum"]), "value outside enum"
        )
    if kind == "object":
        props = spec.get("properties", {})
        require(set(spec.get("required", [])) <= value.keys(), "required field missing")
        require(
            spec.get("additionalProperties", True) or value.keys() <= props.keys(),
            "unexpected field",
        )
        for key in value.keys() & props.keys():
            validate(value[key], props[key])
    if kind == "array":
        for item in value:
            validate(item, spec["items"])


def tool_map(tools):
    require(isinstance(tools, list) and len(tools) <= 16, "invalid tools")
    result = {}
    for tool in tools:
        require(
            isinstance(tool, dict)
            and set(tool) <= {"name", "description", "parameters"},
            "invalid tool",
        )
        name = identifier(tool.get("name"), "tool name")
        require(
            name not in result
            and isinstance(tool.get("parameters"), dict)
            and tool["parameters"].get("type") == "object"
            and isinstance(tool.get("description", ""), str),
            "invalid tool contract",
        )
        schema(tool["parameters"])
        result[name] = tool
    return result


def openrouter_policy(zero_cost):
    policy = {"allow_fallbacks": False, "require_parameters": True}
    if zero_cost:
        policy["max_price"] = {"prompt": 0, "completion": 0, "request": 0}
    return policy


def payload(route, content):
    provider = route["provider"]
    tools = content["tools"]
    spec = content["schema"]
    prompt = content["prompt"]
    messages = [{"role": "user", "content": prompt}]
    body = {"model": route["model"], "stream": content["stream"], "messages": messages}
    limit_key = (
        "max_completion_tokens" if provider in {"openai", "groq"} else "max_tokens"
    )
    body[limit_key] = content["max_output_tokens"]
    previous = content["tool_results"]
    if provider == "chatgpt":
        require(
            not (content["stream"] or tools or spec or previous),
            "registered Responses adapter supports plain text only",
        )
        return {
            "model": route["model"],
            "input": prompt,
            "max_output_tokens": content["max_output_tokens"],
            "store": False,
        }
    if provider == "anthropic":
        if tools:
            body["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]
        if spec:
            body["output_config"] = {"format": {"type": "json_schema", "schema": spec}}
        if previous:
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": c["id"],
                                "name": c["name"],
                                "input": c["arguments"],
                            }
                            for c in previous
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": c["id"],
                                "content": c["result"],
                            }
                            for c in previous
                        ],
                    },
                ]
            )
        return body
    if previous:
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": encode(c["arguments"]),
                        },
                    }
                    for c in previous
                ],
            }
        )
        messages.extend(
            {"role": "tool", "tool_call_id": c["id"], "content": c["result"]}
            for c in previous
        )
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
    if spec:
        if provider == "deepseek":
            body["response_format"] = {"type": "json_object"}
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": "Return JSON matching this schema: " + encode(spec),
                },
            )
        else:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": spec},
            }
    if provider == "openrouter":
        body["provider"] = openrouter_policy(content.get("zero_cost"))
    if content["stream"]:
        body["stream_options"] = {"include_usage": True}
    return body


class Reply:
    """Accumulate one completion, including incremental tool arguments and usage."""

    def __init__(self, emit):
        self.text = ""
        self.calls = {}
        self.usage = {}
        self.reported_cost_usd = None
        self.finish = None
        self.emit = emit

    def add_text(self, text):
        if text:
            require(isinstance(text, str), "invalid provider text")
            self.text += text
            require(len(self.text) <= 262144, "completion too large")
            self.emit(text)

    def usage_from(self, raw):
        details = (
            raw.get("completion_tokens_details")
            or raw.get("output_tokens_details")
            or {}
        )
        reasoning = (
            details.get("reasoning_tokens") if isinstance(details, dict) else None
        )
        if type(reasoning) is int and reasoning >= 0:
            self.usage["reasoning_tokens"] = reasoning
        cost = raw.get("cost")
        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
            self.reported_cost_usd = cost
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_read_input_tokens", "cache_read_input_tokens"),
            ("cache_creation_input_tokens", "cache_creation_input_tokens"),
        ):
            if source in raw:
                value = raw[source]
                require(type(value) is int and value >= 0, "invalid usage")
                self.usage[target] = value

    def tool(self, index, ident="", name="", arguments=""):
        require(type(index) is int and 0 <= index < 64, "invalid tool index")
        call = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        for key, fragment in (("id", ident), ("name", name), ("arguments", arguments)):
            if fragment:
                call[key] += fragment

    def responses(self, event, stream):
        require(
            not stream and event.get("status") == "completed",
            "incomplete Responses result",
        )
        for item in event.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        self.add_text(part["text"])
        self.usage_from(event.get("usage", {}))
        self.finish = "stop"

    def chat(self, event, stream):
        self.usage_from(
            event.get("usage") or (event.get("x_groq") or {}).get("usage") or {}
        )
        for choice in event.get("choices", []):
            require(choice.get("index", 0) == 0, "unexpected completion index")
            msg = choice["delta" if stream else "message"]
            self.add_text(msg.get("content"))
            for i, t in enumerate(msg.get("tool_calls") or []):
                f = t.get("function") or {}
                self.tool(
                    t.get("index", i), t.get("id"), f.get("name"), f.get("arguments")
                )
            self.finish = choice.get("finish_reason") or self.finish

    def block(self, index, b, streaming):
        if b["type"] == "text":
            self.add_text(b["text"])
        elif b["type"] == "tool_use":
            self.tool(
                index,
                b["id"],
                b["name"],
                "" if streaming and not b["input"] else encode(b["input"]),
            )

    def anthropic(self, event, stream):
        if not stream:
            self.usage_from(event["usage"])
            self.finish = event["stop_reason"]
            for i, b in enumerate(event["content"]):
                self.block(i, b, False)
        elif event["type"] == "message_start":
            self.usage_from(event["message"]["usage"])
        elif event["type"] == "content_block_start":
            self.block(event["index"], event["content_block"], True)
        elif event["type"] == "content_block_delta":
            delta = event["delta"]
            if delta["type"] == "text_delta":
                self.add_text(delta["text"])
            elif delta["type"] == "input_json_delta":
                self.tool(event["index"], arguments=delta["partial_json"])
        elif event["type"] == "message_delta":
            self.usage_from(event.get("usage", {}))
            self.finish = event["delta"].get("stop_reason")

    def result(self, content):
        require(self.finish, "incomplete provider response")
        if self.calls or content["schema"]:
            require(
                self.finish
                in {"stop", "tool_calls", "end_turn", "tool_use", "stop_sequence"},
                "incomplete structured or tool response",
            )
        tools = tool_map(content["tools"])
        calls = []
        for _, call in sorted(self.calls.items()):
            require(
                call["name"] in tools
                and call["id"]
                and all(c["id"] != call["id"] for c in calls),
                "unrecognized tool call",
            )
            args = json.loads(call["arguments"] or "{}")
            validate(args, tools[call["name"]]["parameters"])
            calls.append(dict(call, arguments=args))
        result = {
            "text": self.text,
            "tool_calls": calls,
            "finish_reason": self.finish,
            "usage": self.usage,
        }
        if content["schema"]:
            value = json.loads(self.text)
            validate(value, content["schema"])
            result["structured"] = value
        return result
