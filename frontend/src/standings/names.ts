// What the audience calls an entrant, and its lab's colour. This mirrors
// castle/show.py (model_name, seat_colour): labs are told apart by name and
// colour, never by logo, and a reference bot is grey and never named as a model.

export const BOT_PREFIX = "bot:";
export const BOT_COLOUR = "#8A8F98";
export const BOT_LAB = "The bot cast";

const LAB_COLOURS: Record<string, string> = {
  anthropic: "#D97757",
  openai: "#10A37F",
  google: "#4C8BF5",
  "x-ai": "#C9CED6",
  deepseek: "#7B8CFF",
  moonshotai: "#B07CFF",
  qwen: "#F2B134",
  "z-ai": "#2EC4B6",
  meta: "#5AA9FF",
  mistralai: "#FA6E2A",
};
const SPARE_COLOURS = ["#E06C9F", "#9CCB5A", "#56C7E8", "#E8C547", "#C38D6B"];

const LAB_NAMES: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  "x-ai": "xAI",
  deepseek: "DeepSeek",
  moonshotai: "Moonshot AI",
  qwen: "Qwen",
  "z-ai": "Z.ai",
  meta: "Meta",
  mistralai: "Mistral",
};

const CASED: Record<string, string> = { gpt: "GPT", glm: "GLM", deepseek: "DeepSeek", oss: "OSS" };
const VERSION = /^[a-z]?\d[\d.]*[a-z]?$/;

export function isBot(entrant: string): boolean {
  return entrant.startsWith(BOT_PREFIX);
}

/** "anthropic/claude-opus-5.5" is "Claude Opus 5.5"; "openai/gpt-6.1-sol" is
 * "GPT-6.1 Sol". */
export function modelName(model: string): string {
  const slug = (model.split("/").pop() ?? model).split(":")[0];
  const tokens: string[] = [];
  for (const token of slug.split("-")) {
    const last = tokens[tokens.length - 1];
    if (/^\d+$/.test(token) && last !== undefined && /^\d+$/.test(last.replace(/\./g, ""))) {
      tokens[tokens.length - 1] = `${last}.${token}`;
    } else if (token) {
      tokens.push(token);
    }
  }
  const words: string[] = [];
  for (const token of tokens) {
    let word: string;
    if (token in CASED) word = CASED[token];
    else if (/^o\d/.test(token)) word = token;
    else if (VERSION.test(token)) word = token.toUpperCase();
    else word = token.slice(0, 1).toUpperCase() + token.slice(1);
    if (words.length > 0 && words[words.length - 1] === "GPT" && /^\d/.test(word)) {
      words[words.length - 1] += `-${word}`;
    } else {
      words.push(word);
    }
  }
  return words.join(" ") || model;
}

function labSlug(entrant: string): string {
  return entrant.replace(/^model:/, "").split("/")[0];
}

/** The name on the page: a model's, or "Bot p3" for a reference bot profile. */
export function entrantName(entrant: string): string {
  if (isBot(entrant)) return `Bot ${entrant.split("/").pop() ?? entrant}`;
  return modelName(entrant.replace(/^model:/, ""));
}

export function labName(entrant: string): string {
  if (isBot(entrant)) return BOT_LAB;
  const slug = labSlug(entrant);
  return LAB_NAMES[slug] ?? slug;
}

export function labColour(entrant: string): string {
  if (isBot(entrant)) return BOT_COLOUR;
  const slug = labSlug(entrant);
  if (slug in LAB_COLOURS) return LAB_COLOURS[slug];
  let sum = 0;
  for (const byte of new TextEncoder().encode(slug)) sum += byte;
  return SPARE_COLOURS[sum % SPARE_COLOURS.length];
}
