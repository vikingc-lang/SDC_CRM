/** Types for the help center (backend: app/services/help.py, app/help/content.py). */
export interface HowTo { q: string; steps: string[] }
export interface HelpArea {
  key: string; title: string; icon: string; pages: string[]; roles: string[]; summary: string; capabilities: string[]; howto: HowTo[];
  processes: { key: string; title: string }[];
}
export interface ProcessStep { id: string; label: string; lane: string; page: string; detail: string }
export interface HelpProcess { key: string; title: string; summary: string; lanes: string[]; steps: ProcessStep[] }
export interface RoleGuide {
  key: string; title: string; mission: string; day: { label: string; href: string }[]; areas: { key: string; title: string }[];
  checklist: string[]; tips: string[];
}
export interface HelpCatalog {
  role: RoleGuide | null; roles: Record<string, string>; areas: HelpArea[]; processes: HelpProcess[]; glossary: Record<string, string>;
  shortcuts: { keys: string; action: string }[]; whats_new: { date: string; title: string; items: string[] }[];
  permissions: { resource: string; label: string; actions: string[]; scope: string }[];
}
export interface HelpHit { kind: "area" | "howto" | "process" | "glossary"; title: string; snippet: string; href: string; area?: string; steps?: string[]; page?: string }
export interface DataModel {
  entities: { table: string; label: string; description: string; area: string; links: string[];
    fields: { key: string; label: string; kind: string; required: boolean }[]; custom_fields: { key: string; label: string; kind: string }[] }[];
  custom_objects: { key: string; label: string; plural: string; description: string | null; fields: { key: string; label: string; kind: string }[] }[];
}
