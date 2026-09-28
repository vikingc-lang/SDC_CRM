/**
 * Page translation for everything the key-based strings (`useT`) don't cover yet.
 *
 * English is the source language. For another language, a phrase catalogue (lib/phrases/<lang>.json: exact English
 * phrase → translation, plus a few patterns for phrases with numbers) is loaded on demand, and the rendered page's
 * text, placeholders, titles and accessible labels are translated in place. A MutationObserver keeps up as React
 * renders: when React writes new English into a node, it is translated again. Record data is left alone unless it
 * happens to equal a UI phrase exactly; mark data with translate="no" (or class "notranslate") to exclude it, which
 * code, keyboard hints and editable fields always are.
 */
import type { Lang } from "@/lib/i18n";

interface Catalogue { phrases: Record<string, string>; patterns: [string, string][] }

const LOADERS: Partial<Record<Lang, () => Promise<Catalogue>>> = {
  es: () => import("@/lib/phrases/es.json").then((m) => m.default as unknown as Catalogue),
  fr: () => import("@/lib/phrases/fr.json").then((m) => m.default as unknown as Catalogue),
  de: () => import("@/lib/phrases/de.json").then((m) => m.default as unknown as Catalogue),
  hi: () => import("@/lib/phrases/hi.json").then((m) => m.default as unknown as Catalogue),
};

const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "CODE", "PRE", "KBD", "TEXTAREA", "NOSCRIPT", "SVG"]);
const ATTRS = ["placeholder", "title", "aria-label"] as const;

export class PageTranslator {
  private phrases: Map<string, string>;
  private patterns: [RegExp, string][];
  private already: Set<string>;  // text already in the target language (never translated twice: German "Start" ≠ English "Start")
  private shown = new WeakMap<Node, string>();  // text node -> the translation we wrote
  private attrShown = new WeakMap<Element, Record<string, string>>();
  private observer: MutationObserver | null = null;
  private pending = new Set<Node>();
  private frame = 0;

  constructor(cat: Catalogue, translated: string[] = []) {
    this.phrases = new Map(Object.entries(cat.phrases));
    this.already = new Set([...Object.values(cat.phrases), ...translated].map((t) => t.replace(/\s+/g, " ").trim()));
    this.patterns = cat.patterns.map(([re, to]) => [new RegExp(`^${re}$`), to]);
  }

  /** The translation of one English phrase, or null when there is none. */
  lookup(text: string): string | null {
    const key = text.replace(/\s+/g, " ").trim();
    if (!key || !/[A-Za-z]/.test(key) || this.already.has(key)) return null;
    const hit = this.phrases.get(key);
    if (hit !== undefined) return hit;
    const stripped = key.replace(/[.:…]$/, "");
    if (stripped !== key) {
      const h = this.phrases.get(stripped);
      if (h !== undefined) return h + key.slice(stripped.length);
    }
    for (const [re, to] of this.patterns) {
      if (re.test(key)) return key.replace(re, (...m) => to.replace(/\$(\d)/g, (_, i) => this.lookup(String(m[Number(i)])) ?? String(m[Number(i)])));
    }
    return null;
  }

  private excluded(el: Element | null): boolean {
    for (let e = el; e; e = e.parentElement) {
      if (SKIP_TAGS.has(e.tagName.toUpperCase()) || e.getAttribute("translate") === "no" || e.classList.contains("notranslate")
        || (e as HTMLElement).isContentEditable) return true;
    }
    return false;
  }

  private text(node: Text) {
    const value = node.nodeValue ?? "";
    if (this.shown.get(node) === value) return;  // already ours
    if (this.excluded(node.parentElement)) return;
    const t = this.lookup(value);
    if (t === null) return;
    const lead = value.match(/^\s*/)?.[0] ?? "", trail = value.match(/\s*$/)?.[0] ?? "";
    const next = lead + t + trail;
    this.shown.set(node, next);
    if (next !== value) node.nodeValue = next;
  }

  private attrs(el: Element) {
    if (this.excluded(el)) return;
    const mine = this.attrShown.get(el) ?? {};
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v || mine[a] === v) continue;
      const t = this.lookup(v);
      if (t !== null) {
        mine[a] = t;
        if (t !== v) el.setAttribute(a, t);
      }
    }
    this.attrShown.set(el, mine);
  }

  translate(root: Node) {
    if (root.nodeType === Node.TEXT_NODE) return this.text(root as Text);
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    const el = root as Element;
    if (root.nodeType === Node.ELEMENT_NODE) this.attrs(el);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) this.text(n as Text);
      else this.attrs(n as Element);
    }
  }

  start(root: HTMLElement = document.body) {
    this.translate(root);
    const title = this.lookup(document.title.split(" · ")[0]);
    if (title) document.title = document.title.replace(document.title.split(" · ")[0], title);
    this.observer = new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "characterData") this.pending.add(r.target);
        else if (r.type === "attributes") this.pending.add(r.target);
        else r.addedNodes.forEach((n) => this.pending.add(n));
      }
      if (!this.frame) this.frame = requestAnimationFrame(() => this.flush());
    });
    this.observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: [...ATTRS] });
  }

  private flush() {
    this.frame = 0;
    const nodes = [...this.pending];
    this.pending.clear();
    for (const n of nodes) if (n.isConnected) this.translate(n);
    this.observer?.takeRecords();  // our own writes
  }

  stop() {
    this.observer?.disconnect();
    this.observer = null;
    if (this.frame) cancelAnimationFrame(this.frame);
  }
}

export async function loadTranslator(lang: Lang, translated: string[] = []): Promise<PageTranslator | null> {
  const load = LOADERS[lang];
  if (!load) return null;
  return new PageTranslator(await load(), translated);
}
