"use client";

/**
 * Localisation: the UI language plus number, currency, date and time formats follow the user's locale
 * (Settings → Language and region; the browser's language until they choose). Formatting helpers in lib/utils
 * read the active locale from here, so every page gets local formats; translated strings come from `useT()`.
 * English is the source language: a key missing in another language falls back to English, then to the key.
 */
import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo } from "react";
import { get, getToken } from "@/lib/api";
import type { Me } from "@/lib/types";

export const LOCALES = [
  { code: "en-US", label: "English (United States)" },
  { code: "en-GB", label: "English (United Kingdom)" },
  { code: "en-IN", label: "English (India)" },
  { code: "es-ES", label: "Español (España)" },
  { code: "es-MX", label: "Español (México)" },
  { code: "fr-FR", label: "Français (France)" },
  { code: "de-DE", label: "Deutsch (Deutschland)" },
  { code: "hi-IN", label: "हिन्दी (भारत)" },
] as const;
export type Lang = "en" | "es" | "fr" | "de" | "hi";

type Dict = Record<string, string>;

const en: Dict = {
  "nav.home": "Home", "nav.leads": "Leads", "nav.pipeline": "Pipeline", "nav.accounts": "Accounts", "nav.contacts": "Contacts", "nav.tasks": "Tasks",
  "nav.ask": "Ask Aiden", "nav.quotes": "Quotes", "nav.approvals": "Approvals", "nav.orders": "Orders", "nav.products": "Products",
  "nav.performance": "Quotas & commission", "nav.reports": "Reports", "nav.campaigns": "Campaigns", "nav.cases": "Service", "nav.knowledge": "Knowledge",
  "nav.success": "Customer success", "nav.finance": "Finance & ERP", "nav.partners": "Partners", "nav.admin": "Admin",
  "nav.help": "Help center", "shell.help": "Help",
  "group.revenue": "Revenue", "group.marketing": "Marketing", "group.customers": "Customers", "group.workspace": "Workspace", "group.objects": "Custom objects",
  "shell.quick_log": "Quick-Log", "shell.search": "Search, or paste meeting notes to log…", "shell.settings": "Settings & AI engine", "shell.sign_out": "Sign out",
  "shell.notifications": "Notifications", "shell.unread": "{n} unread", "shell.caught_up": "You are all caught up.", "shell.suite": "SDC Solutions suite",
  "shell.portfolio": "SDC Solutions portfolio", "shell.current": "Current", "shell.open_menu": "Open menu", "shell.close_menu": "Close menu",
  "common.save": "Save", "common.cancel": "Cancel", "common.delete": "Delete", "common.remove": "Remove", "common.add": "Add", "common.approve": "Approve",
  "common.reject": "Reject", "common.saved": "Saved", "common.none": "None", "common.browser_default": "Browser default",
  "settings.title": "Settings", "settings.region": "Language and region",
  "settings.region_hint": "The language of menus and labels, and how numbers, amounts and dates are written for you.",
  "settings.language": "Language and formats", "settings.timezone": "Time zone", "settings.example": "Example",
  "settings.saved": "Preferences saved", "settings.calendar": "Calendar sync",
  "settings.calendar_hint": "Two-way sync with your Google or Microsoft 365 calendar: meetings with CRM contacts appear in Cirra, and meetings you log in Cirra appear in your calendar.",
  "settings.connect": "Connect {provider}", "settings.disconnect": "Disconnect", "settings.sync_now": "Sync now", "settings.last_sync": "Last synced {when}",
  "settings.not_configured": "Not set up on this server", "settings.never_synced": "Not synced yet",
  "approvals.ai": "AI suggestions", "approvals.quotes": "Quote approvals",
  "deal.products": "Products", "deal.team": "Deal team", "deal.splits": "Splits",
};

const es: Dict = {
  "nav.home": "Inicio", "nav.leads": "Prospectos", "nav.pipeline": "Embudo", "nav.accounts": "Cuentas", "nav.contacts": "Contactos", "nav.tasks": "Tareas",
  "nav.ask": "Preguntar a Aiden", "nav.quotes": "Cotizaciones", "nav.approvals": "Aprobaciones", "nav.orders": "Pedidos", "nav.products": "Productos",
  "nav.performance": "Cuotas y comisiones", "nav.reports": "Informes", "nav.campaigns": "Campañas", "nav.cases": "Servicio", "nav.knowledge": "Conocimiento",
  "nav.success": "Éxito del cliente", "nav.finance": "Finanzas y ERP", "nav.partners": "Socios", "nav.admin": "Administración",
  "nav.help": "Centro de ayuda", "shell.help": "Ayuda",
  "group.revenue": "Ingresos", "group.marketing": "Marketing", "group.customers": "Clientes", "group.workspace": "Espacio de trabajo", "group.objects": "Objetos personalizados",
  "shell.quick_log": "Registro rápido", "shell.search": "Busca o pega notas de reunión para registrarlas…", "shell.settings": "Configuración y motor de IA",
  "shell.sign_out": "Cerrar sesión", "shell.notifications": "Notificaciones", "shell.unread": "{n} sin leer", "shell.caught_up": "Estás al día.",
  "shell.suite": "Suite SDC Solutions", "shell.portfolio": "Cartera de SDC Solutions", "shell.current": "Actual", "shell.open_menu": "Abrir menú", "shell.close_menu": "Cerrar menú",
  "common.save": "Guardar", "common.cancel": "Cancelar", "common.delete": "Eliminar", "common.remove": "Quitar", "common.add": "Añadir", "common.approve": "Aprobar",
  "common.reject": "Rechazar", "common.saved": "Guardado", "common.none": "Ninguno", "common.browser_default": "Predeterminado del navegador",
  "settings.title": "Configuración", "settings.region": "Idioma y región",
  "settings.region_hint": "El idioma de los menús y etiquetas, y cómo se muestran los números, importes y fechas.",
  "settings.language": "Idioma y formatos", "settings.timezone": "Zona horaria", "settings.example": "Ejemplo", "settings.saved": "Preferencias guardadas",
  "settings.calendar": "Sincronización de calendario",
  "settings.calendar_hint": "Sincronización bidireccional con tu calendario de Google o Microsoft 365: las reuniones con contactos del CRM aparecen en Cirra y las que registras en Cirra aparecen en tu calendario.",
  "settings.connect": "Conectar {provider}", "settings.disconnect": "Desconectar", "settings.sync_now": "Sincronizar ahora", "settings.last_sync": "Última sincronización {when}",
  "settings.not_configured": "No configurado en este servidor", "settings.never_synced": "Aún sin sincronizar",
  "approvals.ai": "Sugerencias de IA", "approvals.quotes": "Aprobaciones de cotizaciones",
  "deal.products": "Productos", "deal.team": "Equipo de la oportunidad", "deal.splits": "Reparto de crédito",
};

const fr: Dict = {
  "nav.home": "Accueil", "nav.leads": "Pistes", "nav.pipeline": "Pipeline", "nav.accounts": "Comptes", "nav.contacts": "Contacts", "nav.tasks": "Tâches",
  "nav.ask": "Demander à Aiden", "nav.quotes": "Devis", "nav.approvals": "Approbations", "nav.orders": "Commandes", "nav.products": "Produits",
  "nav.performance": "Quotas et commissions", "nav.reports": "Rapports", "nav.campaigns": "Campagnes", "nav.cases": "Service", "nav.knowledge": "Base de connaissances",
  "nav.success": "Succès client", "nav.finance": "Finance et ERP", "nav.partners": "Partenaires", "nav.admin": "Administration",
  "nav.help": "Centre d'aide", "shell.help": "Aide",
  "group.revenue": "Revenus", "group.marketing": "Marketing", "group.customers": "Clients", "group.workspace": "Espace de travail", "group.objects": "Objets personnalisés",
  "shell.quick_log": "Saisie rapide", "shell.search": "Rechercher, ou coller des notes de réunion à enregistrer…", "shell.settings": "Paramètres et moteur d'IA",
  "shell.sign_out": "Se déconnecter", "shell.notifications": "Notifications", "shell.unread": "{n} non lues", "shell.caught_up": "Vous êtes à jour.",
  "shell.suite": "Suite SDC Solutions", "shell.portfolio": "Portefeuille SDC Solutions", "shell.current": "Actuel", "shell.open_menu": "Ouvrir le menu", "shell.close_menu": "Fermer le menu",
  "common.save": "Enregistrer", "common.cancel": "Annuler", "common.delete": "Supprimer", "common.remove": "Retirer", "common.add": "Ajouter", "common.approve": "Approuver",
  "common.reject": "Refuser", "common.saved": "Enregistré", "common.none": "Aucun", "common.browser_default": "Par défaut du navigateur",
  "settings.title": "Paramètres", "settings.region": "Langue et région",
  "settings.region_hint": "La langue des menus et libellés, et l'écriture des nombres, montants et dates.",
  "settings.language": "Langue et formats", "settings.timezone": "Fuseau horaire", "settings.example": "Exemple", "settings.saved": "Préférences enregistrées",
  "settings.calendar": "Synchronisation du calendrier",
  "settings.calendar_hint": "Synchronisation dans les deux sens avec votre agenda Google ou Microsoft 365 : les réunions avec des contacts du CRM apparaissent dans Cirra, et celles saisies dans Cirra apparaissent dans votre agenda.",
  "settings.connect": "Connecter {provider}", "settings.disconnect": "Déconnecter", "settings.sync_now": "Synchroniser", "settings.last_sync": "Dernière synchronisation {when}",
  "settings.not_configured": "Non configuré sur ce serveur", "settings.never_synced": "Pas encore synchronisé",
  "approvals.ai": "Suggestions de l'IA", "approvals.quotes": "Approbations de devis",
  "deal.products": "Produits", "deal.team": "Équipe de l'opportunité", "deal.splits": "Répartition du crédit",
};

const de: Dict = {
  "nav.home": "Start", "nav.leads": "Leads", "nav.pipeline": "Pipeline", "nav.accounts": "Firmen", "nav.contacts": "Kontakte", "nav.tasks": "Aufgaben",
  "nav.ask": "Aiden fragen", "nav.quotes": "Angebote", "nav.approvals": "Freigaben", "nav.orders": "Aufträge", "nav.products": "Produkte",
  "nav.performance": "Quoten und Provisionen", "nav.reports": "Berichte", "nav.campaigns": "Kampagnen", "nav.cases": "Service", "nav.knowledge": "Wissensdatenbank",
  "nav.success": "Customer Success", "nav.finance": "Finanzen und ERP", "nav.partners": "Partner", "nav.admin": "Verwaltung",
  "nav.help": "Hilfe-Center", "shell.help": "Hilfe",
  "group.revenue": "Umsatz", "group.marketing": "Marketing", "group.customers": "Kunden", "group.workspace": "Arbeitsbereich", "group.objects": "Eigene Objekte",
  "shell.quick_log": "Schnellerfassung", "shell.search": "Suchen oder Besprechungsnotizen zum Erfassen einfügen…", "shell.settings": "Einstellungen und KI",
  "shell.sign_out": "Abmelden", "shell.notifications": "Benachrichtigungen", "shell.unread": "{n} ungelesen", "shell.caught_up": "Alles erledigt.",
  "shell.suite": "SDC Solutions Suite", "shell.portfolio": "SDC Solutions Portfolio", "shell.current": "Aktuell", "shell.open_menu": "Menü öffnen", "shell.close_menu": "Menü schließen",
  "common.save": "Speichern", "common.cancel": "Abbrechen", "common.delete": "Löschen", "common.remove": "Entfernen", "common.add": "Hinzufügen", "common.approve": "Freigeben",
  "common.reject": "Ablehnen", "common.saved": "Gespeichert", "common.none": "Keine", "common.browser_default": "Wie im Browser",
  "settings.title": "Einstellungen", "settings.region": "Sprache und Region",
  "settings.region_hint": "Die Sprache von Menüs und Beschriftungen sowie die Schreibweise von Zahlen, Beträgen und Datumsangaben.",
  "settings.language": "Sprache und Formate", "settings.timezone": "Zeitzone", "settings.example": "Beispiel", "settings.saved": "Einstellungen gespeichert",
  "settings.calendar": "Kalendersynchronisierung",
  "settings.calendar_hint": "Zweiwege-Synchronisierung mit Ihrem Google- oder Microsoft-365-Kalender: Termine mit CRM-Kontakten erscheinen in Cirra, in Cirra erfasste Termine in Ihrem Kalender.",
  "settings.connect": "{provider} verbinden", "settings.disconnect": "Trennen", "settings.sync_now": "Jetzt synchronisieren", "settings.last_sync": "Zuletzt synchronisiert {when}",
  "settings.not_configured": "Auf diesem Server nicht eingerichtet", "settings.never_synced": "Noch nicht synchronisiert",
  "approvals.ai": "KI-Vorschläge", "approvals.quotes": "Angebotsfreigaben",
  "deal.products": "Produkte", "deal.team": "Opportunity-Team", "deal.splits": "Provisionsaufteilung",
};

const hi: Dict = {
  "nav.home": "होम", "nav.leads": "लीड", "nav.pipeline": "पाइपलाइन", "nav.accounts": "खाते", "nav.contacts": "संपर्क", "nav.tasks": "कार्य",
  "nav.ask": "Aiden से पूछें", "nav.quotes": "कोटेशन", "nav.approvals": "स्वीकृतियाँ", "nav.orders": "ऑर्डर", "nav.products": "उत्पाद",
  "nav.performance": "कोटा और कमीशन", "nav.reports": "रिपोर्ट", "nav.campaigns": "अभियान", "nav.cases": "सेवा", "nav.knowledge": "ज्ञानकोष",
  "nav.success": "ग्राहक सफलता", "nav.finance": "वित्त और ERP", "nav.partners": "साझेदार", "nav.admin": "प्रशासन",
  "nav.help": "सहायता केंद्र", "shell.help": "सहायता",
  "group.revenue": "राजस्व", "group.marketing": "मार्केटिंग", "group.customers": "ग्राहक", "group.workspace": "कार्यक्षेत्र", "group.objects": "कस्टम ऑब्जेक्ट",
  "shell.quick_log": "त्वरित लॉग", "shell.search": "खोजें, या लॉग करने के लिए मीटिंग नोट्स चिपकाएँ…", "shell.settings": "सेटिंग्स और AI इंजन",
  "shell.sign_out": "साइन आउट", "shell.notifications": "सूचनाएँ", "shell.unread": "{n} अपठित", "shell.caught_up": "सब कुछ देख लिया गया है।",
  "shell.suite": "SDC Solutions सुइट", "shell.portfolio": "SDC Solutions पोर्टफ़ोलियो", "shell.current": "वर्तमान", "shell.open_menu": "मेनू खोलें", "shell.close_menu": "मेनू बंद करें",
  "common.save": "सहेजें", "common.cancel": "रद्द करें", "common.delete": "हटाएँ", "common.remove": "निकालें", "common.add": "जोड़ें", "common.approve": "स्वीकृत करें",
  "common.reject": "अस्वीकार करें", "common.saved": "सहेजा गया", "common.none": "कोई नहीं", "common.browser_default": "ब्राउज़र के अनुसार",
  "settings.title": "सेटिंग्स", "settings.region": "भाषा और क्षेत्र",
  "settings.region_hint": "मेनू और लेबल की भाषा, और संख्याएँ, राशियाँ और तारीखें किस प्रकार लिखी जाती हैं।",
  "settings.language": "भाषा और प्रारूप", "settings.timezone": "समय क्षेत्र", "settings.example": "उदाहरण", "settings.saved": "प्राथमिकताएँ सहेजी गईं",
  "settings.calendar": "कैलेंडर सिंक",
  "settings.calendar_hint": "आपके Google या Microsoft 365 कैलेंडर के साथ दोतरफ़ा सिंक: CRM संपर्कों के साथ मीटिंग Cirra में दिखती हैं, और Cirra में दर्ज मीटिंग आपके कैलेंडर में।",
  "settings.connect": "{provider} कनेक्ट करें", "settings.disconnect": "डिस्कनेक्ट करें", "settings.sync_now": "अभी सिंक करें", "settings.last_sync": "अंतिम सिंक {when}",
  "settings.not_configured": "इस सर्वर पर सेट नहीं है", "settings.never_synced": "अभी तक सिंक नहीं हुआ",
  "approvals.ai": "AI सुझाव", "approvals.quotes": "कोटेशन स्वीकृतियाँ",
  "deal.products": "उत्पाद", "deal.team": "डील टीम", "deal.splits": "क्रेडिट विभाजन",
};

export const MESSAGES: Record<Lang, Dict> = { en, es, fr, de, hi };

// ---- the active locale (read by the formatting helpers in lib/utils) ----------------------------------------

let active = { locale: "en-US", timeZone: undefined as string | undefined };

export function currentLocale(): string {
  return active.locale;
}

export function currentTimeZone(): string | undefined {
  return active.timeZone;
}

export function langOf(locale: string): Lang {
  const l = locale.slice(0, 2).toLowerCase();
  return (l in MESSAGES ? l : "en") as Lang;
}

export function supported(locale: string | null | undefined): string {
  if (!locale) return "en-US";
  const exact = LOCALES.find((l) => l.code.toLowerCase() === locale.toLowerCase());
  if (exact) return exact.code;
  const same = LOCALES.find((l) => l.code.slice(0, 2) === locale.slice(0, 2).toLowerCase());
  return same?.code ?? "en-US";
}

export function translate(lang: Lang, key: string, vars?: Record<string, string | number>): string {
  const raw = MESSAGES[lang][key] ?? en[key] ?? key;
  return vars ? raw.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? "")) : raw;
}

const Ctx = createContext<{ locale: string; lang: Lang; timeZone?: string }>({ locale: "en-US", lang: "en" });

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const signedIn = typeof window !== "undefined" && !!getToken();
  const me = useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/users/me"), enabled: signedIn, staleTime: 60_000 });
  const browser = typeof navigator !== "undefined" ? navigator.language : "en-US";
  const locale = supported(me.data?.preferences?.locale || browser);
  const timeZone = me.data?.preferences?.timezone || undefined;
  active = { locale, timeZone };  // formatting helpers read this synchronously during render
  const value = useMemo(() => ({ locale, lang: langOf(locale), timeZone }), [locale, timeZone]);
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  // Changing language re-renders the whole tree once so every formatted value follows.
  return <Ctx.Provider value={value}><div key={`${locale}|${timeZone ?? ""}`} className="contents">{children}</div></Ctx.Provider>;
}

export function useLocale() {
  return useContext(Ctx);
}

/** `t("nav.home")` or `t("shell.unread", { n: 3 })` in the user's language. */
export function useT() {
  const { lang } = useContext(Ctx);
  return (key: string, vars?: Record<string, string | number>) => translate(lang, key, vars);
}
