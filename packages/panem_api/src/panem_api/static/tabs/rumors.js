// District rumors and investigating them -- Discord-only for now
// (`/rumor list|investigate`, `panem_bot.cogs.rumors`). Not built out as
// a dashboard tab yet.
import { mountPlaceholder } from "./_placeholder.js?v=7";

export function mount(root) {
  return mountPlaceholder(root, "Rumors", "/rumor list or /rumor investigate");
}
