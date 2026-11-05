"""Known model reasoning-effort capabilities and local validation.

The Dashboard supports OpenAI-compatible gateways, so unknown model aliases
must remain configurable.  This module only applies a strict allowlist when a
model name matches a capability verified from the vendor's official docs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


REASONING_EFFORT_CAPABILITIES_VERIFIED_ON = "2026-08-13"


@dataclass(frozen=True)
class ReasoningEffortCapability:
    """Documented reasoning-effort inputs for one model family."""

    key: str
    provider: str
    models: str
    model_pattern: str
    accepted_efforts: tuple[str, ...]
    effective_efforts: tuple[str, ...]
    default_effort: str
    chat_parameter_style: str = "reasoning_effort"
    mappings: tuple[tuple[str, str], ...] = ()
    note: str = ""
    source_url: str = ""
    verified_on: str = REASONING_EFFORT_CAPABILITIES_VERIFIED_ON

    def effective_effort(self, effort: str) -> str:
        return dict(self.mappings).get(effort, effort)


# Keep specific variants before their broader base families.  Patterns accept
# official date-stamped aliases but deliberately avoid arbitrary gateway
# suffixes; an unrecognized alias falls back to free-form compatibility.
REASONING_EFFORT_CAPABILITIES: tuple[ReasoningEffortCapability, ...] = (
    ReasoningEffortCapability(
        key="gpt-6.1-sol",
        provider="OpenAI",
        models="gpt-6.1-sol",
        model_pattern=r"^gpt-6\.1-sol$",
        accepted_efforts=("low", "medium", "high", "xhigh", "max"),
        effective_efforts=("low", "medium", "high", "xhigh", "max"),
        default_effort="medium",
        note="不支持 none/minimal；自动使用 Responses，输出上限包含推理 tokens，默认 Standard 计费。",
        source_url="https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        verified_on="2026-11-05",
    ),
    ReasoningEffortCapability(
        key="qwen-3.8-max",
        provider="阿里云 Qwen",
        models="qwen3.8-max",
        model_pattern=r"^qwen3\.8-max$",
        accepted_efforts=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        effective_efforts=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        default_effort="xhigh",
        chat_parameter_style="qwen_38_reasoning_effort",
        note="Responses 原生支持 7 档；Chat 原生为 low/medium/xhigh，并将 minimal→low、high/max→xhigh、none→关闭。",
        source_url="https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions",
    ),
    ReasoningEffortCapability(
        key="qwen-responses",
        provider="阿里云 Qwen",
        models="Qwen 3.5–3.7、Qwen3 Max、Qwen Plus/Flash/Coder 常用 Responses 型号",
        model_pattern=(
            r"^(?:qwen3\.7-(?:max(?:-2026-(?:05-20|06-08))?|"
            r"plus(?:-2026-05-26)?|flash(?:-2026-07-15)?)|"
            r"qwen3\.6-(?:plus(?:-2026-04-02)?|flash(?:-2026-04-16)?|35b-a3b)|"
            r"qwen3\.5-(?:plus(?:-2026-(?:02-15|04-20))?|"
            r"flash(?:-2026-02-23)?|397b-a17b|122b-a10b|27b|35b-a3b)|"
            r"qwen3-max(?:-2026-01-23)?|qwen3-coder-(?:plus|flash)|"
            r"qwen-(?:plus|flash))$"
        ),
        accepted_efforts=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        effective_efforts=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        default_effort="xhigh",
        chat_parameter_style="qwen_enable_thinking",
        note="自动模式使用 Responses 保留 7 档；强制 Chat 时 none 转为关闭，其余档位均转为开启。xhigh/max 仅北京和新加坡地域支持。",
        source_url="https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses",
    ),
    ReasoningEffortCapability(
        key="qwen-chat-default-off",
        provider="阿里云 Qwen",
        models="Qwen3 Max Preview、Qwen Plus/Flash/Turbo 其他常用 Chat 型号",
        model_pattern=(
            r"^(?:qwen3-max-preview|qwen-plus-latest|"
            r"qwen-(?:plus|flash|turbo)-\d{4}-\d{2}-\d{2}|qwen-turbo)$"
        ),
        accepted_efforts=("disabled", "enabled"),
        effective_efforts=("disabled", "enabled"),
        default_effort="disabled",
        chat_parameter_style="qwen_enable_thinking",
        note="Chat Completions 使用顶层 enable_thinking 布尔开关，没有多档思考强度。",
        source_url="https://help.aliyun.com/zh/model-studio/deep-thinking",
    ),
    ReasoningEffortCapability(
        key="qwen-chat-default-on",
        provider="阿里云 Qwen",
        models="Qwen3.6 Max Preview、Qwen3.7 US 与 Qwen3 常用开源混合思考型号",
        model_pattern=(
            r"^(?:qwen3\.6-max-preview|qwen3\.7-(?:max|plus)-us|"
            r"qwen3-(?:235b-a22b|32b|30b-a3b|14b|8b))$"
        ),
        accepted_efforts=("disabled", "enabled"),
        effective_efforts=("disabled", "enabled"),
        default_effort="enabled",
        chat_parameter_style="qwen_enable_thinking",
        note="Chat Completions 使用顶层 enable_thinking 布尔开关；部分开源型号仅支持流式思考输出。",
        source_url="https://help.aliyun.com/zh/model-studio/deep-thinking",
    ),
    ReasoningEffortCapability(
        key="qwen-thinking-only",
        provider="阿里云 Qwen",
        models="Qwen3.7 Max Preview、Qwen3 Thinking、QwQ Plus",
        model_pattern=(
            r"^(?:qwen3\.7-max-(?:preview|2026-05-17)|"
            r"qwen3-next-80b-a3b-thinking|qwen3-(?:235b-a22b|30b-a3b)-thinking-2507|"
            r"qwq-plus)$"
        ),
        accepted_efforts=(),
        effective_efforts=("always-on",),
        default_effort="always-on",
        note="仅思考模式，不能关闭或调节强度；请留空使用模型固定行为。",
        source_url="https://help.aliyun.com/zh/model-studio/deep-thinking",
    ),
    ReasoningEffortCapability(
        key="minimax-m3",
        provider="MiniMax",
        models="MiniMax-M3",
        model_pattern=r"^minimax-m3$",
        accepted_efforts=("none", "minimal", "low", "medium", "high"),
        effective_efforts=("none", "adaptive"),
        default_effort="Chat: adaptive；Responses: none",
        chat_parameter_style="minimax_m3_thinking",
        mappings=(
            ("minimal", "adaptive"),
            ("low", "adaptive"),
            ("medium", "adaptive"),
            ("high", "adaptive"),
        ),
        note="none 关闭；其余值仅为兼容输入，都会开启 Adaptive Thinking，不会改变思考深度。",
        source_url="https://platform.minimax.io/docs/api-reference/responses-create",
    ),
    ReasoningEffortCapability(
        key="minimax-m2",
        provider="MiniMax",
        models="MiniMax-M2 / M2.1 / M2.5 / M2.7（含 highspeed）",
        model_pattern=r"^minimax-m2(?:\.(?:1|5|7)(?:-highspeed)?)?$",
        accepted_efforts=("none", "minimal", "low", "medium", "high"),
        effective_efforts=("always-on",),
        default_effort="always-on",
        chat_parameter_style="minimax_always_thinking",
        mappings=(
            ("none", "always-on"),
            ("minimal", "always-on"),
            ("low", "always-on"),
            ("medium", "always-on"),
            ("high", "always-on"),
        ),
        note="M2.x 始终思考且不能关闭；Responses 接受兼容值但实际行为不变，Chat 不发送控制字段。",
        source_url="https://platform.minimax.io/docs/api-reference/responses-create",
    ),
    ReasoningEffortCapability(
        key="glm-5.2",
        provider="智谱 AI",
        models="glm-5.2",
        model_pattern=r"^glm-5\.2$",
        accepted_efforts=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        effective_efforts=("none", "high", "max"),
        default_effort="max",
        chat_parameter_style="thinking_with_reasoning_effort",
        mappings=(
            ("minimal", "none"),
            ("low", "high"),
            ("medium", "high"),
            ("xhigh", "max"),
        ),
        note="reasoning_effort 仅在 Thinking 开启时生效；请求会自动补充 thinking.type=enabled。",
        source_url="https://docs.bigmodel.cn/cn/guide/capabilities/thinking",
    ),
    ReasoningEffortCapability(
        key="glm-thinking-toggle",
        provider="智谱 AI",
        models="GLM 4.5–5.1 常用文本/视觉型号",
        model_pattern=(
            r"^(?:glm-5(?:\.1|-turbo)?|glm-5v-turbo|"
            r"glm-4\.7(?:-flashx)?|glm-4\.6v?|"
            r"glm-4\.5(?:-(?:air|airx|flash|x)|v)?)$"
        ),
        accepted_efforts=("disabled", "enabled"),
        effective_efforts=("disabled", "enabled"),
        default_effort="enabled",
        chat_parameter_style="thinking_type",
        note="这些型号只有思考开关，没有官方多档 reasoning_effort。",
        source_url="https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode",
    ),
    ReasoningEffortCapability(
        key="mimo-v2.5",
        provider="小米 MiMo",
        models="mimo-v2.5 / mimo-v2.5-pro",
        model_pattern=r"^mimo-v2\.5(?:-pro)?$",
        accepted_efforts=("none", "low", "medium", "high"),
        effective_efforts=("none", "enabled"),
        default_effort="enabled",
        chat_parameter_style="mimo_thinking_type",
        mappings=(("low", "enabled"), ("medium", "enabled"), ("high", "enabled")),
        note="Responses API 接受四个值；目前 low/medium/high 均为相同的开启思考效果。",
        source_url="https://mimo.mi.com/docs/zh-CN/api/chat/responses",
    ),
    ReasoningEffortCapability(
        key="deepseek-v4-pro",
        provider="DeepSeek",
        models="deepseek-v4-pro",
        model_pattern=r"^deepseek-v4-pro$",
        accepted_efforts=("low", "medium", "high", "xhigh", "max"),
        effective_efforts=("high", "max"),
        default_effort="high",
        mappings=(("low", "high"), ("medium", "high"), ("xhigh", "max")),
        note="当前官方说明中 Pro 实际支持 high/max，其余列出的兼容值会映射。",
        source_url="https://api-docs.deepseek.com/api/create-chat-completion/",
    ),
    ReasoningEffortCapability(
        key="deepseek-v4-flash",
        provider="DeepSeek",
        models="deepseek-v4-flash",
        model_pattern=r"^deepseek-v4-flash$",
        accepted_efforts=("low", "medium", "high", "xhigh", "max"),
        effective_efforts=("low", "high", "max"),
        default_effort="high",
        mappings=(("medium", "high"), ("xhigh", "high")),
        note="medium/xhigh 是兼容输入，按官方说明映射为 high。",
        source_url="https://api-docs.deepseek.com/api/create-chat-completion/",
    ),
    ReasoningEffortCapability(
        key="grok-4.3",
        provider="xAI",
        models="grok-4.3 / grok-4.3-latest / grok-latest",
        model_pattern=r"^(?:grok-4\.3(?:-latest)?|grok-latest)$",
        accepted_efforts=("none", "low", "medium", "high"),
        effective_efforts=("none", "low", "medium", "high"),
        default_effort="",
        note="支持关闭推理；官方型号页未注明直接调用时的统一默认值。",
        source_url="https://docs.x.ai/developers/models/grok-4.3",
    ),
    ReasoningEffortCapability(
        key="grok-4.5",
        provider="xAI",
        models="grok-4.5（含 latest/日期别名）",
        model_pattern=r"^grok-4\.5(?:-latest|-\d{4}-\d{2}-\d{2})?$",
        accepted_efforts=("low", "medium", "high"),
        effective_efforts=("low", "medium", "high"),
        default_effort="high",
        note="该模型不能通过思考强度关闭推理。",
        source_url="https://docs.x.ai/docs/guides/reasoning",
    ),
    ReasoningEffortCapability(
        key="gpt-5.6",
        provider="OpenAI",
        models="gpt-5.6 / sol / terra / luna",
        model_pattern=(
            r"^gpt-5\.6(?:-(?:sol|terra|luna))?"
            r"(?:-\d{4}-\d{2}-\d{2})?$"
        ),
        accepted_efforts=("none", "low", "medium", "high", "xhigh", "max"),
        effective_efforts=("none", "low", "medium", "high", "xhigh", "max"),
        default_effort="medium",
        source_url="https://developers.openai.com/api/docs/models/gpt-5.6-sol",
    ),
    ReasoningEffortCapability(
        key="gpt-5.4-pro",
        provider="OpenAI",
        models="gpt-5.4-pro",
        model_pattern=r"^gpt-5\.4-pro(?:-\d{4}-\d{2}-\d{2})?$",
        accepted_efforts=("medium", "high", "xhigh"),
        effective_efforts=("medium", "high", "xhigh"),
        default_effort="medium",
        source_url="https://developers.openai.com/api/docs/models/gpt-5.4-pro",
    ),
    ReasoningEffortCapability(
        key="gpt-5.4",
        provider="OpenAI",
        models="gpt-5.4 / mini / nano",
        model_pattern=(
            r"^gpt-5\.4(?:-(?:mini|nano))?"
            r"(?:-\d{4}-\d{2}-\d{2})?$"
        ),
        accepted_efforts=("none", "low", "medium", "high", "xhigh"),
        effective_efforts=("none", "low", "medium", "high", "xhigh"),
        default_effort="none",
        source_url="https://developers.openai.com/api/docs/models/gpt-5.4",
    ),
    ReasoningEffortCapability(
        key="gpt-5.2-pro",
        provider="OpenAI",
        models="gpt-5.2-pro",
        model_pattern=r"^gpt-5\.2-pro(?:-\d{4}-\d{2}-\d{2})?$",
        accepted_efforts=("medium", "high", "xhigh"),
        effective_efforts=("medium", "high", "xhigh"),
        default_effort="medium",
        source_url="https://developers.openai.com/api/docs/models/gpt-5.2-pro",
    ),
    ReasoningEffortCapability(
        key="gpt-5.2",
        provider="OpenAI",
        models="gpt-5.2",
        model_pattern=r"^gpt-5\.2(?:-\d{4}-\d{2}-\d{2})?$",
        accepted_efforts=("none", "low", "medium", "high", "xhigh"),
        effective_efforts=("none", "low", "medium", "high", "xhigh"),
        default_effort="none",
        source_url="https://developers.openai.com/api/docs/models/gpt-5.2",
    ),
    ReasoningEffortCapability(
        key="gpt-5.1",
        provider="OpenAI",
        models="gpt-5.1",
        model_pattern=r"^gpt-5\.1(?:-\d{4}-\d{2}-\d{2})?$",
        accepted_efforts=("none", "low", "medium", "high"),
        effective_efforts=("none", "low", "medium", "high"),
        default_effort="none",
        source_url="https://developers.openai.com/api/docs/models/gpt-5.1",
    ),
    ReasoningEffortCapability(
        key="gpt-5-pro",
        provider="OpenAI",
        models="gpt-5-pro",
        model_pattern=r"^gpt-5-pro(?:-\d{4}-\d{2}-\d{2})?$",
        accepted_efforts=("high",),
        effective_efforts=("high",),
        default_effort="high",
        source_url="https://developers.openai.com/api/docs/models/gpt-5-pro",
    ),
    ReasoningEffortCapability(
        key="gpt-5",
        provider="OpenAI",
        models="gpt-5",
        model_pattern=r"^gpt-5(?:-\d{4}-\d{2}-\d{2})?$",
        accepted_efforts=("minimal", "low", "medium", "high"),
        effective_efforts=("minimal", "low", "medium", "high"),
        default_effort="",
        source_url="https://developers.openai.com/api/docs/models/gpt-5",
    ),
)


class UnsupportedReasoningEffortError(ValueError):
    """A known model was configured with an undocumented effort value."""

    def __init__(
        self,
        model: str,
        effort: str,
        capability: ReasoningEffortCapability,
    ) -> None:
        self.model = model
        self.effort = effort
        self.capability = capability
        allowed = _allowed_efforts_text(capability)
        super().__init__(
            f"模型 {model} 不支持思考强度“{effort}”；允许值：{allowed}；"
            "也可留空使用模型默认值"
        )


@dataclass(frozen=True)
class ReasoningEffortResolution:
    """Normalized input and the value that the vendor will effectively use."""

    configured_effort: str
    effective_effort: str
    capability: ReasoningEffortCapability | None


def normalize_reasoning_effort(value: Any) -> str:
    """Normalize a provider-defined reasoning-effort token."""

    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", normalized):
        raise ValueError("思考强度只能包含字母、数字、点、下划线或连字符，且不超过 64 个字符")
    return normalized


def reasoning_effort_capability(model: str) -> ReasoningEffortCapability | None:
    """Return strict capability data only for a recognized model name."""

    normalized_model = str(model or "").strip().lower()
    if not normalized_model:
        return None
    for capability in REASONING_EFFORT_CAPABILITIES:
        if re.fullmatch(capability.model_pattern, normalized_model):
            return capability
    return None


def resolve_model_reasoning_effort(
    model: str,
    value: Any,
) -> ReasoningEffortResolution:
    """Normalize and validate effort for known models, preserving unknown ones."""

    effort = normalize_reasoning_effort(value)
    capability = reasoning_effort_capability(model)
    if effort and capability and effort not in capability.accepted_efforts:
        raise UnsupportedReasoningEffortError(str(model or "").strip(), effort, capability)
    effective_effort = capability.effective_effort(effort) if capability and effort else effort
    return ReasoningEffortResolution(effort, effective_effort, capability)


def effective_reasoning_effort(model: str, value: Any, *, api_mode: str) -> str:
    """Return the effective reasoning behavior for the selected wire protocol."""

    resolution = resolve_model_reasoning_effort(model, value)
    effort = resolution.configured_effort
    capability = resolution.capability
    if not effort or capability is None:
        return resolution.effective_effort
    if api_mode == "responses":
        if capability.key in {"qwen-3.8-max", "qwen-responses"}:
            return effort
        return resolution.effective_effort
    if capability.key == "qwen-3.8-max":
        return {
            "none": "none",
            "minimal": "low",
            "low": "low",
            "medium": "medium",
            "high": "xhigh",
            "xhigh": "xhigh",
            "max": "xhigh",
        }[effort]
    if capability.key == "qwen-responses":
        return "none" if effort == "none" else "enabled"
    return resolution.effective_effort


def chat_reasoning_request_settings(model: str, value: Any) -> dict[str, Any]:
    """Build vendor-correct Chat Completions thinking parameters."""

    resolution = resolve_model_reasoning_effort(model, value)
    effort = resolution.configured_effort
    if not effort:
        return {}
    style = (
        resolution.capability.chat_parameter_style
        if resolution.capability
        else "reasoning_effort"
    )
    if style == "thinking_type":
        return {"thinking": {"type": effort}}
    if style == "thinking_with_reasoning_effort":
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": effort,
        }
    if style == "mimo_thinking_type":
        return {
            "thinking": {
                "type": "disabled" if effort == "none" else "enabled",
            }
        }
    if style == "qwen_38_reasoning_effort":
        if effort == "none":
            return {"enable_thinking": False}
        return {"reasoning_effort": effort}
    if style == "qwen_enable_thinking":
        return {
            "enable_thinking": effort not in {"none", "disabled"},
        }
    if style == "minimax_m3_thinking":
        return {
            "thinking": {
                "type": "disabled" if effort == "none" else "adaptive",
            }
        }
    if style == "minimax_always_thinking":
        return {}
    return {"reasoning_effort": effort}


def _allowed_efforts_text(capability: ReasoningEffortCapability) -> str:
    if not capability.accepted_efforts:
        return "无（仅可留空使用模型固定行为）"
    mappings = dict(capability.mappings)
    return "、".join(
        f"{effort}（映射为 {mappings[effort]}）"
        if effort in mappings
        else effort
        for effort in capability.accepted_efforts
    )


def reasoning_effort_capability_catalog() -> list[dict[str, Any]]:
    """Return serializable, non-sensitive reference data for docs and UIs."""

    return [
        {
            "key": capability.key,
            "provider": capability.provider,
            "models": capability.models,
            "model_pattern": capability.model_pattern,
            "accepted_efforts": list(capability.accepted_efforts),
            "effective_efforts": list(capability.effective_efforts),
            "default_effort": capability.default_effort,
            "chat_parameter_style": capability.chat_parameter_style,
            "mappings": dict(capability.mappings),
            "note": capability.note,
            "source_url": capability.source_url,
            "verified_on": capability.verified_on,
        }
        for capability in REASONING_EFFORT_CAPABILITIES
    ]
