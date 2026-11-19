import openai from "./assets/model-icons/openai.svg?inline";
import claude from "./assets/model-icons/claude.svg?inline";
import gemini from "./assets/model-icons/gemini.svg?inline";
import gemma from "./assets/model-icons/gemma.svg?inline";
import kimi from "./assets/model-icons/kimi.svg?inline";
import qwen from "./assets/model-icons/qwen.svg?inline";
import deepseek from "./assets/model-icons/deepseek.svg?inline";
import zai from "./assets/model-icons/zai.svg?inline";
import muse from "./assets/model-icons/muse.svg?inline";

const familyIcons: Record<string, string> = {
  gpt: openai,
  claude,
  gemini,
  gemma,
  kimi,
  qwen,
  deepseek,
  glm: zai,
  muse,
};

export function getModelIcon(name: string): string | undefined {
  const family = name.match(/^[a-z]+/i)?.[0].toLowerCase();
  return family ? familyIcons[family] : undefined;
}

const openRouterIds: Record<string, string> = {
  "gpt-6.2-astra": "openai/gpt-6.2-astra",
  "gpt-6.2-sol": "openai/gpt-6.2-sol",
  "gpt-6.1-terra": "openai/gpt-6.1-terra",
  "claude-fable-5-2": "anthropic/claude-fable-5.2",
  "claude-opus-5-5": "anthropic/claude-opus-5.5",
  "claude-sonnet-5-5": "anthropic/claude-sonnet-5.5",
  "gemini-4-hydrogen": "google/gemini-4-hydrogen",
  "gemini-4-carbon": "google/gemini-4-carbon",
  "gemma-4-31b-it": "google/gemma-4-31b-it",
  "kimi-k3.1-pro": "moonshotai/kimi-k3.1-pro",
  "qwen4-max": "qwen/qwen4-max",
  "qwen3.8-flash-next": "qwen/qwen3.8-flash",
  "qwen3.8-27b": "qwen/qwen3.8-27b",
  "deepseek-v4.1-flash-1017": "deepseek/deepseek-v4.1-flash-1017",
  "glm-5.3-flashx": "z-ai/glm-5.3-flashx",
  "muse-glimmer-30b": "meta/muse-glimmer-30b",
};

export function modelUrl(id: string, name: string) {
  const slug = openRouterIds[id];
  return slug
    ? "https://openrouter.ai/" + slug
    : "https://openrouter.ai/models?q=" + encodeURIComponent(name);
}

type ModelNameProps = { id: string; name: string };

export function ModelName({ id, name }: ModelNameProps) {
  const icon = getModelIcon(name);
  return (
    <a
      className="model-name"
      href={modelUrl(id, name)}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={name + " · OpenRouter (새 탭)"}
    >
      {icon && <img className="model-icon" src={icon} alt="" width={18} height={18} />}
      <span>{name}</span>
    </a>
  );
}
