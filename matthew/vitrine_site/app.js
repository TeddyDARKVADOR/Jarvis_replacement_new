// Vitrine de Matthew — lecture seule. Lit data/vitrine.json (généré par matthew/vitrine.py,
// liste blanche vérifiée) et anime l'examen du jour dans une scène three.js.
// Tout le texte de la page est en HTML : la 3D n'est qu'un plus, et s'efface si WebGL manque
// ou si le visiteur demande moins de mouvement. Français et anglais (bouton FR / EN).

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const OG = new URLSearchParams(location.search).has("og");          // capture pour l'image de partage
// Écran tactile (téléphone, tablette, en portrait comme en paysage) : la scène ne reçoit plus le
// doigt (CSS : pointer-events none), sinon défiler devient presque impossible ; un toucher
// court sur une lumière ou un bâtiment est rattrapé au niveau du document (pickAt).
const TOUCH = matchMedia("(pointer: coarse)").matches;
document.documentElement.classList.toggle("touch", TOUCH);
const $ = (id) => document.getElementById(id);
const pct = (x) => (x == null ? "—" : Math.round(x * 100) + " %");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ── Langue ───────────────────────────────────────────────────────────────────
const I18N = {
  fr: {
    doc_title: "Matthew — une IA qui apprend chaque nuit",
    skip: "Aller aux détails", tagline: "Une IA qui apprend chaque nuit, sous vos yeux.",
    hero_num: "de bonnes réponses à son dernier examen", scroll: "Faites défiler ↓",
    c1_k: "1 · Sa maison", c1_h: "Voici sa base, sur la Lune.",
    c1_p: "Matthew vit sur un ordinateur de salon. Ici, on le montre comme une base : au centre, son esprit ; autour, tout ce qu'il sait faire.",
    c1_f: "savoir-faire appris sur",
    c2_k: "2 · Ses savoir-faire", c2_h: "Chaque lumière est un savoir-faire.",
    c2_p: "Ouvrir une application, retrouver un fichier, chercher la météo, envoyer un message, se souvenir… Plus la lumière est verte, mieux il le maîtrise. Éteinte : il ne l'a pas encore appris.",
    c2_hint: "Touchez une lumière ou un bâtiment pour y voler.",
    c3_k: "3 · Ses nuits", c3_h: "Chaque nuit, il apprend.",
    c3_p: "Pendant que tout le monde dort, il s'entraîne sur de nouveaux exemples. Regardez sa base s'allumer, nuit après nuit.",
    c4_k: "4 · Son examen", c4_h: "Puis il passe un examen.",
    c4_p: "Des questions inventées pour l'occasion, souvent jamais vues. Pour chacune, il doit choisir le bon savoir-faire :",
    c5_k: "5 · Ses progrès", c5_h: "Et il ne garde que ses progrès.",
    c5_p: "Une nouvelle version ne remplace l'ancienne que si elle fait mieux à l'examen. Sinon, rien ne change.",
    c6_h: "Rien d'autre n'est montré.",
    c6_p: "Aucune vraie conversation n'est publiée ici : tout ce que vous voyez est inventé pour l'examen. Et il n'apprend jamais en copiant Gemini ou Claude — seulement auprès de modèles libres et des corrections de son utilisateur.",
    c6_more: "Pour les curieux : tous les détails ↓", d_h: "Pour les curieux", st_streak: "Nuits d'affilée",

    kicker: "Apprentissage en public · lecture seule",
    lead: "Une IA personnelle qui se construit sous vos yeux. Chaque nuit, elle s'entraîne, passe un examen, et ne prend la relève que si elle fait mieux que la veille. Chaque amélioration apparaît ici. Vous regardez ; personne ne peut lui parler d'ici.",
    tl_title: "Frise des nuits", tl_play: "Rejouer", tl_stop: "Arrêter", tl_aria: "Choisir une nuit",
    tl_sum: (n, total, s) => ` · ${n}/${total} outils appris${s != null ? ` · score ${s.toFixed(2)}` : ""}`,
    scene_aria: "Scène 3D : Matthew sur la Lune, ses outils autour, la Terre et les planètes dans le ciel. Chaque scénario de l'examen part du centre vers l'outil choisi.",
    st_score: "Score de l'examen", st_tool: "Bon savoir-faire choisi", st_tools: "Savoir-faire appris", st_ex: "Exemples vérifiés", st_nights: "Nuits", st_changes: "Améliorations (30 j)",
    exam_today: "Examen du jour", days_aria: "Rejouer l'examen d'un autre jour", pause: "Pause", play: "Lecture",
    counters_aria: "Compteurs", c_tokens: "mots écrits par Matthew (tokens)", c_gpu: "heures d'entraînement",
    c_nights: "nuits d'apprentissage", c_streak: "nuits d'affilée",
    h_progress: "Progrès", p_progress: "Score de chaque candidat à l'examen, nuit après nuit. Un point plein : il a été promu et sert depuis.",
    lg_score: "Score global", lg_tool: "Bon outil", lg_args: "Bons arguments", chart_title: "Courbe du score par nuit",
    first_night: "Première nuit — la courbe se dessine nuit après nuit",
    h_ba: "Avant / après", p_ba: "La même phrase, posée à Matthew deux nuits différentes. Ses réponses, telles quelles.",
    ba_empty: "Les premières réponses arrivent avec le prochain examen.",
    ba_single: (d) => `La comparaison apparaîtra dès la prochaine nuit (seule réponse : ${d}).`,
    h_skills: "Tous ses savoir-faire", p_skills: "La barre : sa précision au dernier examen. Grisé : pas encore appris. Un clic ouvre la fiche.",
    h_exam: "L'examen, jour par jour", p_exam: "Le même examen chaque nuit. « Jamais vu » : une demande que Matthew n'a jamais rencontrée en s'entraînant. Toutes les demandes sont inventées pour l'examen ; aucune ne vient d'une vraie conversation.",
    exam_req: "Demande (inventée pour l'examen)", exam_tool: "Attendu",
    h_changes: "Le journal des améliorations", p_changes: "Tout ce qui l'a fait progresser : son intelligence, sa voix, son interface, sa fiabilité. La page se met à jour d'elle-même à chaque amélioration.",
    all: "Tout", more: (n) => `Voir les ${n} autres`, changes_empty: "Les premières améliorations arrivent.",
    h_nights: "Les nuits, une par une", nights_empty: "Pas encore de nuit d'apprentissage.", h_how: "Comment il apprend, en détail",
    steps: "<li><b>Inventer.</b> Un modèle ouvert écrit des demandes réalistes pour chaque outil.</li><li><b>Vérifier.</b> Un autre modèle, à l'aveugle, doit retrouver le bon outil. Sinon, la demande est jetée.</li><li><b>S'entraîner.</b> Sur une carte graphique de salon, depuis un modèle ouvert (Qwen3.5-4B).</li><li><b>Passer l'examen.</b> Sur des demandes jamais vues, outil par outil.</li><li><b>Prendre la relève</b> seulement s'il bat la version précédente. Sinon, rien ne change.</li>",
    rules: "Règles : aucune vraie conversation n'est publiée ici ; aucune réponse de Gemini ou de Claude ne lui sert de modèle — seulement des modèles sous licence ouverte et les corrections de son utilisateur.",
    dr_cases: "Ses questions d'examen", f_updated: "Mis à jour le", f_base: "base",
    credits: "Page en lecture seule, régénérée à chaque amélioration · <a href=\"feed.xml\">flux des améliorations (Atom)</a>. Décor : <a href=\"https://kenney.nl/assets/space-kit\">Space Kit de Kenney</a> et éclairage <a href=\"https://polyhaven.com/a/qwantani_night_puresky\">Poly Haven</a> (CC0) · Matthew en guide : robot de <a href=\"https://quaternius.com\">Tomás Laulhé</a> (CC0) · Lune, planètes et Voie lactée : <a href=\"https://www.solarsystemscope.com/textures/\">Solar System Scope</a> (CC BY 4.0) · rendu <a href=\"https://threejs.org\">three.js</a>.",
    no_data: "Les données du jour ne sont pas encore publiées.", first_exam: "Premier examen ce soir.",
    todo: "à venir", learned: "appris", never_seen: "jamais vu", seen_train: "vu à l'entraînement",
    ok: "réussi", ko: "raté", no_tool: "aucun outil", today: "Aujourd'hui", j7: "J-7",
    count: (i, n, ok, d, ms) => `${i}/${n} · ${ok} réussis le ${d}${ms ? ` · ${ms} s` : ""}`,
    chosen: "choisi", t_chat: "Conversation", t_none: "Refus poli", answered: "a répondu sans outil",
    live_training: "Entraînement en cours", live_export: "Entraînement terminé, empaquetage", epoch: (a, b) => ` — époque ${a}/${b}`,
    next_label: "Prochaine nuit d'apprentissage", next_ics: "Me le rappeler (agenda)", u_h: "h", u_m: "min", u_s: "s",
    next_when: (d, tz) => tz ? `${d} (votre heure) · ${tz} chez Matthew` : d,
    next_wait: "La nuit a commencé : Matthew attend que son ordinateur se réveille.",
    next_off: "Entraînements en pause pour l'instant.",
    ph_prep: "Préparation", "ph_entraînement": "Entraînement", ph_export: "Empaquetage", ph_import: "Installation",
    ph_exam: "Examen", ph_duel: "Duel avec le champion",
    lv_prep: "Il relit ses nouveaux exemples et prépare la nuit.",
    lv_train: (a, b, l) => `Époque ${a} sur ${b}${l != null ? ` · erreur ${l}` : ""} — plus l'erreur baisse, mieux il apprend.`,
    lv_export: "Entraînement terminé : il se met en boîte pour pouvoir répondre.",
    lv_import: "Le nouveau Matthew s'installe à côté de l'ancien.",
    lv_exam: (d, t, r) => `Examen du nouveau Matthew : ${d}/${t} questions, ${r} bonnes réponses.`,
    lv_duel: (d, t, r) => `L'ancien champion passe le même examen : ${d}/${t}, ${r} bonnes réponses.`,
    toast_champ: "Nouveau champion : Matthew vient de progresser !", toast_done: "La nuit est finie : résultats à jour.",
    ics_title: "Matthew apprend (nuit d'entraînement)",
    dr_meta: (ex, n) => `${ex} exemples vérifiés · ${n} examen${n > 1 ? "s" : ""} noté${n > 1 ? "s" : ""}`,
    dr_soon: " · il l'apprendra dans les prochaines nuits", dr_none: "Pas encore de scénario d'examen pour cet outil.",
    share: "Partager", shared: "Lien copié !", share_text: "Regardez une IA apprendre chaque nuit, en public.",
    q_auto: "AUTO", q_high: "HAUTE", q_eco: "ÉCO",
    q_title: { auto: "Qualité automatique : la page s'allège seule si l'ordinateur peine", high: "Qualité haute : tous les effets", eco: "Qualité éco : pour les ordinateurs modestes" },
    q_toast: "La page s'est allégée pour rester fluide (bouton AUTO en haut).",
    tour_btn: "Visite guidée par Matthew",
    sp_k: "Ses spécialités", sp_h: "Il ne prend une tâche que s'il la mérite.",
    sp_p: "Matthew ne remplace pas tout d'un coup. Pour chaque type de tâche, il passe une épreuve à part. Il ne s'en charge que s'il atteint le seuil et fait au moins aussi bien que le modèle qu'il remplacerait.",
    sp_on: "il s'en charge", sp_off: "pas encore", sp_ref: (r) => ` · référence ${r}`, sp_floor: (f) => `seuil ${f}`,
    sp_n: (n) => `${n} cas notés`,
    g_skip: "Passer la visite", g_next: "Suivant", g_end: "C'est parti !", g_voice: "Voix de Matthew",
    g: {
      hello: "Salut ! Moi, c'est Matthew. Je suis une intelligence artificielle qui apprend un peu plus chaque nuit. Je te fais visiter ?",
      base: "Voici ma base, sur la Lune. En vrai, je vis sur un ordinateur de salon… mais avoue que c'est plus joli comme ça. Le noyau bleu, au centre, c'est mon esprit.",
      skills: "Chaque lumière autour de moi est un savoir-faire : ouvrir une application, donner la météo, envoyer un message… Verte : je maîtrise. Éteinte : pas encore appris. Tu pourras toucher une lumière pour y voler.",
      night: "Chaque nuit, pendant que tout le monde dort, je m'entraîne sur de nouveaux exemples. Ce compte à rebours dit quand commence ma prochaine nuit.",
      night_plain: "Chaque nuit, pendant que tout le monde dort, je m'entraîne sur de nouveaux exemples.",
      night_live: "D'ailleurs, je suis en plein entraînement en ce moment ! Tu peux suivre chaque étape ici, en direct.",
      exam: "Ensuite, je passe un examen : des questions inventées, que je n'ai souvent jamais vues. Une nouvelle version de moi ne prend ma place que si elle fait mieux. Je ne régresse jamais.",
      score: (s) => `À mon dernier examen : ${s} de bonnes réponses. Pas encore parfait, mais chaque nuit compte !`,
      spec: "Je me spécialise petit à petit : je ne reprends une tâche que si une épreuve prouve que je la fais bien.",
      bye: "Rien de privé ici : tout ce que tu vois est inventé pour l'examen. Fais défiler pour l'histoire complète. Pour me revoir, clique sur « ? » en haut. Bonne visite !",
    },
  },
  en: {
    doc_title: "Matthew — an AI that learns every night",
    skip: "Skip to the details", tagline: "An AI that learns every night, in front of you.",
    hero_num: "of right answers at its last exam", scroll: "Scroll down ↓",
    c1_k: "1 · Its home", c1_h: "This is its base, on the Moon.",
    c1_p: "Matthew lives on a home computer. Here we show it as a base: its mind in the middle, everything it can do all around.",
    c1_f: "skills learned out of",
    c2_k: "2 · Its skills", c2_h: "Every light is a skill.",
    c2_p: "Opening an app, finding a file, checking the weather, sending a message, remembering… The greener the light, the better it is at it. Off: not learned yet.",
    c2_hint: "Tap a light or a building to fly there.",
    c3_k: "3 · Its nights", c3_h: "Every night, it learns.",
    c3_p: "While everyone sleeps, it trains on new examples. Watch its base light up, night after night.",
    c4_k: "4 · Its exam", c4_h: "Then it sits an exam.",
    c4_p: "Questions made up for the occasion, often never seen before. For each one it must pick the right skill:",
    c5_k: "5 · Its progress", c5_h: "And it only keeps its progress.",
    c5_p: "A new version only replaces the old one if it does better at the exam. Otherwise nothing changes.",
    c6_h: "Nothing else is shown.",
    c6_p: "No real conversation is ever published here: everything you see is made up for the exam. And it never learns by copying Gemini or Claude — only from open models and its user's corrections.",
    c6_more: "For the curious: all the details ↓", d_h: "For the curious", st_streak: "Nights in a row",

    kicker: "Learning in public · read-only",
    lead: "A personal AI being built in front of you. Every night it trains, sits an exam, and only takes over if it beats yesterday's version. Every improvement shows up here. You watch; nobody can talk to it from here.",
    tl_title: "Night by night", tl_play: "Replay", tl_stop: "Stop", tl_aria: "Pick a night",
    tl_sum: (n, total, s) => ` · ${n}/${total} tools learned${s != null ? ` · score ${s.toFixed(2)}` : ""}`,
    scene_aria: "3D scene: Matthew on the Moon, its tools around it, Earth and the planets in the sky. Each exam scenario travels from the centre to the chosen tool.",
    st_score: "Exam score", st_tool: "Right skill picked", st_tools: "Skills learned", st_ex: "Verified examples", st_nights: "Nights", st_changes: "Improvements (30 d)",
    exam_today: "Today's exam", days_aria: "Replay another day's exam", pause: "Pause", play: "Play",
    counters_aria: "Counters", c_tokens: "words written by Matthew (tokens)", c_gpu: "hours of training",
    c_nights: "nights of learning", c_streak: "nights in a row",
    h_progress: "Progress", p_progress: "Each candidate's exam score, night after night. A filled dot: it was promoted and has served since.",
    lg_score: "Overall score", lg_tool: "Right tool", lg_args: "Right arguments", chart_title: "Score per night",
    first_night: "First night — the curve draws itself night after night",
    h_ba: "Before / after", p_ba: "The same sentence, asked to Matthew on two different nights. Its answers, verbatim (in French).",
    ba_empty: "The first answers come with the next exam.",
    ba_single: (d) => `The comparison appears after the next night (only answer so far: ${d}).`,
    h_skills: "All its skills", p_skills: "The bar: accuracy at the last exam. Greyed out: not learned yet. Click for details.",
    h_exam: "The exam, day by day", p_exam: "The same exam every night. \"Never seen\": a request Matthew never met while training. Every request is made up for the exam (in French); none comes from a real conversation.",
    exam_req: "Request (made up for the exam)", exam_tool: "Expected",
    h_changes: "The improvements log", p_changes: "Everything that made it better: its intelligence, voice, interface, reliability (titles in French). The page updates itself after every improvement.",
    all: "All", more: (n) => `Show ${n} more`, changes_empty: "The first improvements are on their way.",
    h_nights: "The nights, one by one", nights_empty: "No learning night yet.", h_how: "How it learns, in detail",
    steps: "<li><b>Invent.</b> An open model writes realistic requests for each tool.</li><li><b>Verify.</b> Another model, blind, must find the right tool. Otherwise the request is thrown away.</li><li><b>Train.</b> On a home graphics card, from an open model (Qwen3.5-4B).</li><li><b>Sit the exam.</b> On requests it has never seen, tool by tool.</li><li><b>Take over</b> only if it beats the previous version. Otherwise nothing changes.</li>",
    rules: "Rules: no real conversation is ever published here; no answer from Gemini or Claude is ever used as a model to learn from — only openly licensed models and its user's corrections.",
    dr_cases: "Its exam questions", f_updated: "Updated", f_base: "base",
    credits: "Read-only page, regenerated after every improvement · <a href=\"feed.xml\">improvements feed (Atom)</a>. Scenery: <a href=\"https://kenney.nl/assets/space-kit\">Kenney's Space Kit</a> and <a href=\"https://polyhaven.com/a/qwantani_night_puresky\">Poly Haven</a> lighting (CC0) · Matthew as a guide: robot by <a href=\"https://quaternius.com\">Tomás Laulhé</a> (CC0) · Moon, planets and Milky Way: <a href=\"https://www.solarsystemscope.com/textures/\">Solar System Scope</a> (CC BY 4.0) · rendered with <a href=\"https://threejs.org\">three.js</a>.",
    no_data: "Today's data is not published yet.", first_exam: "First exam tonight.",
    todo: "coming", learned: "learned", never_seen: "never seen", seen_train: "seen in training",
    ok: "passed", ko: "failed", no_tool: "no tool", today: "Today", j7: "D-7",
    count: (i, n, ok, d, ms) => `${i}/${n} · ${ok} passed on ${d}${ms ? ` · ${ms} s` : ""}`,
    chosen: "chose", t_chat: "Small talk", t_none: "Polite refusal", answered: "answered without a tool",
    live_training: "Training now", live_export: "Training done, packaging", epoch: (a, b) => ` — epoch ${a}/${b}`,
    next_label: "Next learning night", next_ics: "Remind me (calendar)", u_h: "h", u_m: "min", u_s: "s",
    next_when: (d, tz) => tz ? `${d} (your time) · ${tz} at Matthew's` : d,
    next_wait: "The night has started: Matthew is waiting for its computer to wake up.",
    next_off: "Training is paused for now.",
    ph_prep: "Getting ready", "ph_entraînement": "Training", ph_export: "Packaging", ph_import: "Installing",
    ph_exam: "Exam", ph_duel: "Duel with the champion",
    lv_prep: "Reading its new examples and getting the night ready.",
    lv_train: (a, b, l) => `Epoch ${a} of ${b}${l != null ? ` · loss ${l}` : ""} — the lower the loss, the better it learns.`,
    lv_export: "Training done: packing itself up so it can answer.",
    lv_import: "The new Matthew is installed next to the old one.",
    lv_exam: (d, t, r) => `The new Matthew's exam: ${d}/${t} questions, ${r} right.`,
    lv_duel: (d, t, r) => `The current champion takes the same exam: ${d}/${t}, ${r} right.`,
    toast_champ: "New champion: Matthew just got better!", toast_done: "The night is over: results updated.",
    ics_title: "Matthew learns (training night)",
    dr_meta: (ex, n) => `${ex} verified examples · ${n} exam${n > 1 ? "s" : ""} scored`,
    dr_soon: " · it will learn it in the coming nights", dr_none: "No exam scenario for this tool yet.",
    share: "Share", shared: "Link copied!", share_text: "Watch an AI learn every night, in public.",
    q_auto: "AUTO", q_high: "HIGH", q_eco: "ECO",
    q_title: { auto: "Automatic quality: the page lightens itself if the computer struggles", high: "High quality: every effect", eco: "Eco quality: for modest computers" },
    q_toast: "The page lightened itself to stay smooth (AUTO button at the top).",
    tour_btn: "Guided tour by Matthew",
    sp_k: "Its specialties", sp_h: "It only takes a task once it has earned it.",
    sp_p: "Matthew doesn't replace everything at once. Each kind of task has its own test. It only takes one over if it reaches the bar and does at least as well as the model it would replace.",
    sp_on: "it handles it", sp_off: "not yet", sp_ref: (r) => ` · reference ${r}`, sp_floor: (f) => `bar ${f}`,
    sp_n: (n) => `${n} scored cases`,
    g_skip: "Skip the tour", g_next: "Next", g_end: "Let's go!", g_voice: "Matthew's voice",
    g: {
      hello: "Hi! I'm Matthew. I'm an artificial intelligence that learns a little more every night. Want a tour?",
      base: "This is my base, on the Moon. In real life I live on a home computer… but admit it, this looks better. The blue core in the middle is my mind.",
      skills: "Every light around me is a skill: opening an app, giving the weather, sending a message… Green: I've got it. Off: not learned yet. You can tap a light to fly there.",
      night: "Every night, while everyone sleeps, I train on new examples. This countdown says when my next night begins.",
      night_plain: "Every night, while everyone sleeps, I train on new examples.",
      night_live: "In fact, I'm training right now! You can follow every step here, live.",
      exam: "Then I sit an exam: made-up questions, often ones I've never seen. A new version of me only takes over if it does better. I never go backwards.",
      score: (s) => `At my last exam: ${s} right answers. Not perfect yet, but every night counts!`,
      spec: "I'm specialising bit by bit: I only take a task over once a test proves I do it well.",
      bye: "Nothing private here: everything you see is made up for the exam. Scroll for the full story. To see me again, click \"?\" at the top. Enjoy!",
    },
  },
};
function pickLang() {
  const q = new URLSearchParams(location.search).get("lang");
  if (q === "en" || q === "fr") return q;
  try { const s = localStorage.getItem("matthew-lang"); if (s === "en" || s === "fr") return s; } catch { /* stockage indisponible */ }
  return (navigator.language || "fr").toLowerCase().startsWith("fr") ? "fr" : "en";
}
const LANG = pickLang();
const T = (k, ...a) => { const v = I18N[LANG][k] ?? I18N.fr[k] ?? k; return typeof v === "function" ? v(...a) : v; };
const G = (k, ...a) => { const v = I18N[LANG].g[k] ?? I18N.fr.g[k]; return typeof v === "function" ? v(...a) : v; };
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* stockage indisponible */ } },
};

// ── Qualité : la page s'adapte à la machine ──────────────────────────────────
// « auto » (défaut) : un niveau deviné d'après la machine, puis la page s'allège seule si les
// images tardent. « high » et « eco » fixent le niveau (bouton en haut, ou ?q=high|eco|auto).
// Niveaux : 3 tout (halo, définition 1,5, flou des cartes) · 2 sans halo ni flou, définition 1,25
// · 1 définition 1 et 30 images/s · 0 définition 0,75, étiquettes des outils masquées.
const QUALITIES = ["auto", "eco", "high"];
function pickQuality() {
  const q = new URLSearchParams(location.search).get("q");
  if (QUALITIES.includes(q)) return q;
  const s = store.get("matthew-quality");
  return QUALITIES.includes(s) ? s : "auto";
}
let QUALITY = pickQuality();
function guessLevel() {
  if (QUALITY === "high") return 3;
  if (QUALITY === "eco") return 1;
  let lvl = TOUCH || innerWidth < 860 ? 1 : 3;
  const mem = navigator.deviceMemory, cpu = navigator.hardwareConcurrency;
  if ((mem && mem <= 4) || (cpu && cpu <= 4) || (navigator.connection && navigator.connection.saveData)) lvl = Math.min(lvl, 1);
  try {
    const gl = document.createElement("canvas").getContext("webgl");
    const ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
    const gpu = ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : "";
    if (/swiftshader|llvmpipe|software|basic render/i.test(gpu)) lvl = 0;
    // Puces graphiques intégrées : sans halo d'emblée (l'adaptatif descend plus bas si besoin).
    else if (/intel|mali|adreno|powervr|videocore|vega \d+ graphics|radeon\(tm\) graphics/i.test(gpu) && !/\barc\b/i.test(gpu)) lvl = Math.min(lvl, 2);
    const lose = gl && gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
  } catch { /* pas de WebGL : la scène ne démarrera pas */ }
  return lvl;
}
function setQualityClasses(level) {
  document.documentElement.classList.toggle("lite", level <= 2);
  document.documentElement.classList.toggle("minimal", level === 0);
}
const fmtDate = (d) => new Date(d + "T12:00:00").toLocaleDateString(LANG === "fr" ? "fr-FR" : "en-GB", { day: "numeric", month: "short" });
const DOM_EN = { "Système": "System", "Bureau": "Desktop", "Infos": "Info", "Communication": "Messages", "Mémoire": "Memory", "Code": "Code", "Noyau": "Core", "Autres": "Other" };
const AREA_EN = { "IA": "AI", "Interface": "Interface", "Voix": "Voice", "Fiabilité": "Reliability", "Système": "System" };
const OUTCOME_EN = { "nouveau champion": "new champion", "candidat non promu": "candidate not promoted", "en attente de données": "waiting for data",
  "plan prêt (à sec)": "plan ready (dry run)", "échec entraînement": "training failed", "échec données": "data step failed",
  "échec import Ollama": "import failed", "échec promotion": "promotion failed", "occupé": "busy",
  "retour arrière": "rolled back" };
const label = (t) => (LANG === "en" ? t.label_en || t.label : t.label);
const dom = (d) => (LANG === "en" ? DOM_EN[d] || d : d);
const area = (a) => (LANG === "en" ? AREA_EN[a] || a : a);
const outcome = (o) => (LANG === "en" ? OUTCOME_EN[o] || o : o);

let DATA = null;
const toolName = (id) => {
  if (id === "__chat__") return T("t_chat");
  if (id === "__none__") return T("t_none");
  const t = DATA.tools.find((x) => x.id === id);
  return t ? label(t) : id;
};

function applyStatic() {
  document.documentElement.lang = LANG;
  document.title = T("doc_title");
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = T(el.dataset.i18n);
  for (const el of document.querySelectorAll("[data-i18n-html]")) el.innerHTML = T(el.dataset.i18nHtml);
  for (const el of document.querySelectorAll("[data-i18n-aria]")) el.setAttribute("aria-label", T(el.dataset.i18nAria));
  const b = $("lang");
  b.textContent = LANG === "fr" ? "EN" : "FR";
  b.setAttribute("aria-label", LANG === "fr" ? "English version" : "Version française");
  b.onclick = () => {
    const next = LANG === "fr" ? "en" : "fr";
    try { localStorage.setItem("matthew-lang", next); } catch { /* stockage indisponible */ }
    const u = new URL(location.href); u.searchParams.set("lang", next); location.href = u.toString();
  };
  if (OG) document.body.classList.add("og");
  $("tour-btn").title = T("tour_btn");
  renderQualityBtn();
  $("share").onclick = async () => {
    const url = location.origin + location.pathname;
    if (navigator.share) {
      try { await navigator.share({ title: T("doc_title"), text: T("share_text"), url }); } catch { /* annulé */ }
      return;
    }
    try { await navigator.clipboard.writeText(url); toast(T("shared")); } catch { /* presse-papiers refusé */ }
  };
}

function renderQualityBtn() {
  const b = $("quality");
  b.textContent = T("q_" + QUALITY);
  b.title = I18N[LANG].q_title[QUALITY];
  b.setAttribute("aria-label", b.title);
}

async function main() {
  applyStatic();
  try {
    const r = await fetch("data/vitrine.json", { cache: "no-cache" });
    DATA = await r.json();
  } catch (e) {
    $("r-req").textContent = T("no_data");
    return;
  }
  fillStats();
  fillHero();
  drawChart();
  fillCounters();
  fillBeforeAfter();
  fillDomains();
  fillExam();
  fillNights();
  fillChanges();
  fillLive();
  fillNext();
  fillSpecs();
  watchData();
  const closeTool = () => { $("drawer").hidden = true; history.replaceState(null, "", location.pathname + location.search); };
  $("dr-close").onclick = closeTool;
  addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeTool(); });
  const scene = await startScene().catch((e) => { console.warn("3D indisponible :", e); return null; });
  const pick = (id) => { if (scene && !REDUCED) scene.flyToTool(id, () => openTool(id)); else openTool(id); };
  $("domains").addEventListener("click", (e) => { const t = e.target.closest("[data-tool]"); if (t) { openTool(t.dataset.tool); if (scene) scene.flyToTool(t.dataset.tool); } });
  $("domains").addEventListener("keydown", (e) => { const t = e.target.closest("[data-tool]"); if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openTool(t.dataset.tool); } });
  if (scene) scene.onPick(pick);
  if (location.hash.startsWith("#outil-")) pick(decodeURIComponent(location.hash.slice(7)));
  const timeline = startTimeline(scene);
  startReplay(scene);
  startStory(scene, timeline);
  $("quality").onclick = () => {
    QUALITY = QUALITIES[(QUALITIES.indexOf(QUALITY) + 1) % QUALITIES.length];
    store.set("matthew-quality", QUALITY);
    renderQualityBtn();
    if (scene) scene.setQuality(); else setQualityClasses(guessLevel());
  };
  // Première visite : Matthew se présente lui-même (une seule fois ; « ? » le rappelle).
  $("tour-btn").onclick = () => startGuide(scene);
  if (!OG && store.get("matthew-tour") !== "1" && !new URLSearchParams(location.search).has("notour")) {
    setTimeout(() => startGuide(scene), scene ? 900 : 300);
  }
}

// Ses spécialités (Direction 3) : chaque type de tâche a son épreuve, son seuil et sa référence.
function fillSpecs() {
  const S = DATA.specialties;
  $("c-spec").hidden = !(Array.isArray(S) && S.length);
  if ($("c-spec").hidden) return;
  $("specs").innerHTML = S.map((s) => {
    const w = Math.round((s.score ?? 0) * 100), f = Math.round((s.floor ?? 0) * 100);
    const detail = T("sp_floor", pct(s.floor)) + (s.reference != null ? T("sp_ref", pct(s.reference)) : "") + (s.n ? " · " + T("sp_n", s.n) : "");
    return `<li class="${s.delegated ? "on" : ""}"><span>${esc(LANG === "en" ? s.label_en || s.label : s.label)}<span class="st">${esc(T(s.delegated ? "sp_on" : "sp_off"))}</span></span>`
      + `<span class="sv">${s.score == null ? "—" : pct(s.score)}</span><span class="bar"><i style="width:${w}%"></i><b style="left:${f}%"></b></span>`
      + `<span class="sd">${esc(detail)}</span></li>`;
  }).join("");
}

// L'accroche : un seul chiffre, en mots simples.
function fillHero() {
  const c = DATA.champion, s = c && c.score, cov = DATA.coverage;
  $("h-score").textContent = s && s.composite != null ? pct(s.composite) : "—";
  $("f-skills").textContent = cov.tools_covered;
  $("f-total").textContent = cov.tools_total;
}

// Le récit : chaque chapitre qui entre à l'écran déplace la caméra ; la frise se rejoue
// d'elle-même au chapitre des nuits ; des points à droite disent où l'on en est.
function startStory(scene, timeline) {
  const chapters = [...document.querySelectorAll(".chapter:not([hidden])")];
  const dots = $("dots");
  dots.innerHTML = chapters.map(() => "<i></i>").join("");
  let played = false;
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const i = chapters.indexOf(e.target);
      [...dots.children].forEach((d, k) => d.classList.toggle("on", k === i));
      chapters.forEach((c) => c.classList.toggle("active", c === e.target));
      if (scene) scene.goShot(e.target.dataset.shot);
      if (e.target.dataset.shot === "night" && timeline && !played) { played = true; timeline.play(); }
    }
  }, { threshold: 0.55 });
  chapters.forEach((c) => io.observe(c));
}

// ── Chiffres ─────────────────────────────────────────────────────────────────
function fillStats() {
  const c = DATA.champion, s = c && c.score, cov = DATA.coverage;
  $("s-score").textContent = s && s.composite != null ? s.composite.toFixed(2) : "—";
  $("s-tool").textContent = pct(s && s.tool_name);
  $("s-tools").textContent = `${cov.tools_covered}/${cov.tools_total}`;
  $("s-ex").textContent = cov.examples.toLocaleString(LANG === "fr" ? "fr-FR" : "en-GB");
  const month = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  $("s-changes").textContent = (DATA.changes || []).filter((c) => c.date >= month).length || "—";
  $("gen").textContent = DATA.generated;
  $("base").textContent = c ? c.base : "—";
}

// Compteurs vivants : ils comptent jusqu'à leur valeur quand ils entrent à l'écran.
function fillCounters() {
  const c = DATA.counters || {};
  const loc = LANG === "fr" ? "fr-FR" : "en-GB";
  const set = [["c-tokens", c.tokens || 0, 0], ["c-gpu", c.gpu_hours || 0, 1], ["c-nights", c.nights || 0, 0], ["c-streak", c.streak || 0, 0]];
  const run = () => {
    for (const [id, v, dec] of set) {
      const el = $(id), t0 = performance.now(), dur = REDUCED ? 0 : 1600;
      (function step(now) {
        const k = dur ? Math.min(1, (now - t0) / dur) : 1, e = 1 - Math.pow(1 - k, 3);
        el.textContent = (v * e).toLocaleString(loc, { minimumFractionDigits: dec, maximumFractionDigits: dec });
        if (k < 1) requestAnimationFrame(step);
      })(t0);
    }
  };
  const band = document.querySelector(".counters");
  if (!("IntersectionObserver" in window)) return run();
  const io = new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) { io.disconnect(); run(); } }, { threshold: 0.3 });
  io.observe(band);
}

function drawChart() {
  const svg = $("chart"), W = svg.clientWidth || 800, H = 280, P = { l: 40, r: 16, t: 16, b: 30 };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const byDay = new Map();
  for (const h of DATA.history) if (h.score) byDay.set(h.date, h);
  const pts = [...byDay.values()];
  const x = (i) => P.l + (pts.length < 2 ? (W - P.l - P.r) / 2 : (i * (W - P.l - P.r)) / (pts.length - 1));
  const y = (v) => P.t + (1 - v) * (H - P.t - P.b);
  let g = "";
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    g += `<line class="axis" x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${P.l - 8}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(2)}</text>`;
  }
  const series = [["composite", "#5fe3ff", 3], ["tool_name", "#4dffb0", 2], ["tool_args", "#ffb84d", 2]];
  for (const [k, col, w] of series) {
    const p = pts.map((h, i) => [x(i), y(h.score[k] ?? 0)]);
    if (p.length > 1) g += `<polyline fill="none" stroke="${col}" stroke-width="${w}" stroke-linejoin="round" points="${p.map((q) => q.join(",")).join(" ")}" opacity="${k === "composite" ? 1 : 0.7}"/>`;
    pts.forEach((h, i) => {
      const filled = k === "composite" && h.promoted;
      g += `<circle cx="${x(i)}" cy="${y(h.score[k] ?? 0)}" r="${k === "composite" ? 5 : 3}" fill="${filled ? col : "#030810"}" stroke="${col}" stroke-width="2"><title>${fmtDate(h.date)} — ${pct(h.score[k])}</title></circle>`;
    });
  }
  pts.forEach((h, i) => (g += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${fmtDate(h.date)}</text>`));
  if (pts.length === 1) g += `<text x="${W / 2}" y="${P.t + 14}" text-anchor="middle">${esc(T("first_night"))}</text>`;
  svg.innerHTML = `<title id="chart-t">${esc(T("chart_title"))}</title>` + g;
}

// Avant / après : la même phrase, deux nuits, deux réponses (scénarios synthétiques).
function fillBeforeAfter() {
  const box = $("ba");
  const cards = DATA.showcase.filter((s) => s.target === "__chat__" || s.target === "__none__")
    .map((s) => ({ s, days: Object.entries(s.results).filter(([, r]) => r.answer).sort((a, b) => a[0].localeCompare(b[0])) }))
    .filter((c) => c.days.length);
  if (!cards.length) { box.innerHTML = `<p class="muted">${esc(T("ba_empty"))}</p>`; return; }
  box.innerHTML = cards.slice(0, 4).map(({ s, days }) => {
    const last = days[days.length - 1];
    const weekAgo = new Date(new Date(last[0] + "T12:00:00") - 7 * 864e5).toISOString().slice(0, 10);
    const first = days.filter(([d]) => d <= weekAgo).pop() || days[0];
    const col = ([d, r]) => `<div class="ba-col"><span class="label">${fmtDate(d)}</span><p>${esc(r.answer)}</p></div>`;
    return `<article class="ba-card"><p class="ba-q">« ${esc(s.request)} » <span class="badge ghost">${esc(toolName(s.target))}</span></p>
      <div class="ba-cols">${first[0] === last[0] ? col(last) + `<div class="ba-col wait"><p class="muted">${esc(T("ba_single", fmtDate(last[0])))}</p></div>` : col(first) + col(last)}</div></article>`;
  }).join("");
}

function fillDomains() {
  const box = $("domains");
  for (const d of DATA.domains) {
    const tools = DATA.tools.filter((t) => t.domain === d);
    if (!tools.length) continue;
    tools.sort((a, b) => (b.accuracy ?? -1) - (a.accuracy ?? -1));
    const learned = tools.filter((t) => t.examples > 0).length;
    box.insertAdjacentHTML("beforeend", `<div class="domain"><h3>${esc(dom(d))} <small style="color:var(--muted);letter-spacing:0">${learned}/${tools.length}</small></h3>${tools
      .map((t) => {
        const todo = !t.examples;
        const v = todo ? T("todo") : t.accuracy == null ? T("learned") : pct(t.accuracy);
        const w = todo ? 0 : Math.round((t.accuracy ?? 1) * 100);
        return `<div class="tool${todo ? " todo" : ""}" tabindex="0" role="button" data-tool="${esc(t.id)}"><span>${esc(label(t))}</span><span class="v">${v}</span><span class="bar"><i style="width:${w}%"></i></span></div>`;
      }).join("")}</div>`);
  }
}

function examDays() {
  const s = new Set();
  for (const sc of DATA.showcase) for (const d of Object.keys(sc.results)) s.add(d);
  return [...s].sort();
}

function fillExam() {
  const days = examDays().slice(-7);
  let h = `<thead><tr><th scope="col">${esc(T("exam_req"))}</th><th scope="col">${esc(T("exam_tool"))}</th>${days.map((d) => `<th scope="col" class="c">${fmtDate(d)}</th>`).join("")}</tr></thead><tbody>`;
  for (const s of DATA.showcase) {
    h += `<tr><td>${esc(s.request)}${s.heldout ? ` <span class="badge ghost">${esc(T("never_seen"))}</span>` : ""}</td><td>${esc(toolName(s.target))}</td>${days
      .map((d) => {
        const r = s.results[d];
        if (!r) return `<td class="c">·</td>`;
        const title = r.chosen ? `${T("chosen")} : ${toolName(r.chosen)}` : T("answered");
        return `<td class="c ${r.ok ? "ok" : "ko"}" title="${esc(title)}">${r.ok ? "✓" : "✗"}</td>`;
      }).join("")}</tr>`;
  }
  $("exam").innerHTML = h + "</tbody>";
}

function fillNights() {
  const ol = $("nights");
  const rows = [...DATA.cycles].reverse().slice(0, 14);
  if (!rows.length) ol.innerHTML = `<li>${esc(T("nights_empty"))}</li>`;
  for (const c of rows) {
    const win = /champion/.test(c.outcome);
    ol.insertAdjacentHTML("beforeend", `<li><span>${fmtDate(c.date)}</span><span class="o${win ? " win" : ""}">${esc(outcome(c.outcome))}</span></li>`);
  }
}

function fillChanges() {
  const all = DATA.changes || [], chips = $("chips"), ol = $("changes");
  const areas = ["*", ...new Set(all.map((c) => c.area))];
  let sel = "*", shown = 25;
  function render() {
    const rows = all.filter((c) => sel === "*" || c.area === sel);
    ol.innerHTML = rows.slice(0, shown).map((c) => `<li data-area="${esc(c.area)}"><span class="d">${fmtDate(c.date)}</span><span>${esc(c.title)}</span><span class="a">${esc(area(c.area))}</span></li>`).join("")
      + (rows.length > shown ? `<li class="more"><button type="button" id="more">${esc(T("more", rows.length - shown))}</button></li>` : "");
    const more = $("more"); if (more) more.onclick = () => { shown = rows.length; render(); };
  }
  for (const a of areas) {
    const b = document.createElement("button"); b.type = "button"; b.dataset.area = a;
    b.textContent = a === "*" ? T("all") : area(a); b.setAttribute("aria-pressed", String(a === sel));
    b.onclick = () => { sel = a; shown = 25; for (const x of chips.children) x.setAttribute("aria-pressed", String(x.dataset.area === a)); render(); };
    chips.appendChild(b);
  }
  if (!all.length) ol.innerHTML = `<li>${esc(T("changes_empty"))}</li>`; else render();
}

function spark(svg, values, w, h, color) {
  const v = values.filter((x) => x != null);
  if (!v.length) { svg.innerHTML = ""; return; }
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((x, i) => [v.length < 2 ? w / 2 : 4 + (i * (w - 8)) / (v.length - 1), h - 6 - ((x - lo) / span) * (h - 12)]);
  svg.innerHTML = (pts.length > 1 ? `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts.map((q) => q.join(",")).join(" ")}"/>` : "")
    + pts.map((q) => `<circle cx="${q[0]}" cy="${q[1]}" r="2.5" fill="${color}"/>`).join("");
}

const PHASES = ["prep", "entraînement", "export", "import", "exam", "duel"];

function fillLive() {
  const L = DATA.live;
  $("live-line").hidden = !L;
  $("livebox").hidden = !L;
  if (!L) return;
  const ep = L.phase === "entraînement" && L.epoch != null && L.epochs
    ? T("epoch", Math.min(Math.ceil(L.epoch), L.epochs), L.epochs) : "";
  $("live-text").textContent = (L.phase === "entraînement" ? T("live_training") : T("ph_" + L.phase)) + ep;
  const at = PHASES.indexOf(L.phase);
  $("phases").innerHTML = PHASES.map((p, i) =>
    `<li class="${i < at ? "done" : i === at ? "now" : ""}">${i < at ? "✓ " : ""}${esc(T("ph_" + p))}</li>`).join("");
  let frac = 0, text = "";
  const last = (L.losses || []).at(-1);
  if (L.phase === "entraînement") {
    frac = L.epochs ? Math.min(1, (L.epoch || 0) / L.epochs) : 0;
    text = T("lv_train", Math.min(Math.ceil(L.epoch || 0), L.epochs || 0), L.epochs || 0, last != null ? last.toFixed(3) : null);
  } else if (L.phase === "exam" || L.phase === "duel") {
    frac = L.total ? L.done / L.total : 0;
    text = T(L.phase === "exam" ? "lv_exam" : "lv_duel", L.done || 0, L.total || 0, L.right || 0);
  } else {
    frac = { prep: 0.05, export: 1, import: 1 }[L.phase] ?? 0;
    text = T({ prep: "lv_prep", export: "lv_export", import: "lv_import" }[L.phase] || "lv_prep");
  }
  $("live-bar").style.width = Math.round(frac * 100) + "%";
  $("live-detail").textContent = text;
  // La courbe d'erreur ne raconte que l'entraînement.
  const v = ["entraînement", "export"].includes(L.phase) ? L.losses || [] : [], svg = $("live-loss");
  svg.hidden = v.length < 2;
  if (v.length >= 2) {
    const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
    const pts = v.map((x, i) => `${(i * 296) / (v.length - 1) + 2},${46 - ((x - lo) / span) * 42}`).join(" ");
    svg.innerHTML = `<polyline fill="none" stroke="var(--green)" stroke-width="2" points="${pts}"/>`;
  }
}

// ── La prochaine nuit : un vrai compte à rebours ─────────────────────────────
// data/vitrine.json → next.at vient du Planificateur de tâches du PC de Matthew (flywheel.next_run) :
// changer l'heure de la tâche change cette page à la publication suivante. Rien n'est écrit en dur.
let NEXT_TIMER = null;
const two = (n) => String(n).padStart(2, "0");

function fillNext() {
  const N = DATA.next, box = $("next");
  clearInterval(NEXT_TIMER);
  box.hidden = !N || !!DATA.live;
  if (box.hidden) return;
  if (!N.enabled || !N.at) {
    box.classList.add("waiting");
    $("cd-when").textContent = T("next_off");
    $("cd-ics").hidden = true;
    return;
  }
  const when = new Date(N.at);
  // L'heure de Matthew n'est ajoutée que si le visiteur vit dans un autre fuseau.
  const offset = -when.getTimezoneOffset(), theirs = Number(N.at.slice(20, 22)) * 60 + Number(N.at.slice(23, 25));
  const there = offset === (N.at[19] === "-" ? -theirs : theirs) ? "" : N.at.slice(11, 16);
  $("cd-when").textContent = T("next_when", when.toLocaleString(LANG === "en" ? "en-GB" : "fr-FR",
    { weekday: "long", hour: "2-digit", minute: "2-digit" }), there);
  $("cd-ics").hidden = false;
  $("cd-ics").onclick = () => downloadIcs(when);
  const tick = () => {
    const left = Math.round((when - Date.now()) / 1000);
    box.classList.toggle("soon", left > 0 && left < 900);
    box.classList.toggle("waiting", left <= 0);
    if (left <= 0) {
      $("cd-when").textContent = T("next_wait");
      $("cd-ics").hidden = true;
      return;
    }
    $("cd-h").textContent = two(Math.floor(left / 3600));
    $("cd-m").textContent = two(Math.floor((left % 3600) / 60));
    $("cd-s").textContent = two(left % 60);
  };
  tick();
  NEXT_TIMER = setInterval(tick, 1000);
}

function downloadIcs(when) {
  const z = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Matthew//vitrine//FR", "BEGIN:VEVENT",
    `UID:matthew-${z(when)}@vitrine`, `DTSTAMP:${z(new Date())}`, `DTSTART:${z(when)}`, "DURATION:PT1H",
    `SUMMARY:${T("ics_title")}`, `URL:${location.origin}${location.pathname}`, "END:VEVENT", "END:VCALENDAR", ""].join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([ics], { type: "text/calendar" }));
  a.download = "matthew.ics";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ── Rester à jour sans recharger : toutes les minutes autour de la nuit, sinon toutes les 5 min ──
function toast(text) {
  const t = document.createElement("div");
  t.className = "toast";
  t.setAttribute("role", "status");
  t.textContent = text;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 6000);
}

function watchData() {
  if (OG) return;
  const due = () => {
    const at = DATA.next && DATA.next.at ? new Date(DATA.next.at) - Date.now() : Infinity;
    return DATA.live || Math.abs(at) < 30 * 60 * 1000 ? 60000 : 300000;
  };
  const poll = async () => {
    if (!document.hidden) {
      try {
        // ?t= : GitHub Pages garde la page 10 min en cache ; le direct ne peut pas attendre.
        const r = await fetch(`data/vitrine.json?t=${Date.now()}`, { cache: "no-store" });
        const fresh = await r.json();
        if (fresh.generated !== DATA.generated) {
          const champ = (DATA.champion || {}).since !== (fresh.champion || {}).since;
          const ended = DATA.live && !fresh.live;
          DATA = fresh;
          fillHero(); fillStats(); fillCounters(); fillLive(); fillNext(); fillSpecs();
          if (champ) toast(T("toast_champ")); else if (ended) toast(T("toast_done"));
        }
      } catch { /* hors ligne : on réessaie au prochain tour */ }
    }
    setTimeout(poll, due());
  };
  setTimeout(poll, due());
}

function openTool(id) {
  const t = DATA.tools.find((x) => x.id === id);
  if (!t) return;
  $("dr-domain").textContent = dom(t.domain);
  $("dr-title").textContent = label(t);
  $("dr-acc").textContent = !t.examples ? T("todo") : t.accuracy == null ? T("learned") : pct(t.accuracy);
  $("dr-acc").className = "big" + (!t.examples || t.accuracy == null ? " na" : t.accuracy < 0.5 ? " low" : t.accuracy < 0.75 ? " mid" : "");
  const hist = DATA.history.filter((h) => h.per_tool && h.per_tool[id] != null);
  spark($("dr-spark"), hist.map((h) => h.per_tool[id]), 300, 70, "#5fe3ff");
  $("dr-meta").textContent = T("dr_meta", t.examples, hist.length) + (t.examples ? "" : T("dr_soon"));
  const cases = DATA.showcase.filter((x) => x.target === id);
  $("dr-cases").innerHTML = cases.length ? cases.map((c) => `<li>« ${esc(c.request)} »${c.heldout ? ` <span class="badge ghost">${esc(T("never_seen"))}</span>` : ""}<div class="days">${
    Object.entries(c.results).sort().slice(-7).map(([d, r]) => `<span class="d${r.ok ? "" : " ko"}">${fmtDate(d)} ${r.ok ? "✓" : "✗"}</span>`).join(" ")}</div></li>`).join("")
    : `<li class="muted">${esc(T("dr_none"))}</li>`;
  $("drawer").hidden = false;
  $("dr-close").focus();
  history.replaceState(null, "", `${location.pathname}${location.search}#outil-${id}`);
}

// Frise des nuits : un curseur dans le temps ; la base se rallume au fil des outils appris.
function startTimeline(scene) {
  const days = [...new Set([...DATA.tools.map((t) => t.learned_on).filter(Boolean),
    ...DATA.history.map((h) => h.date), ...DATA.cycles.map((c) => c.date)])].sort();
  if (!days.length) return null;
  $("timeline").hidden = false;
  const range = $("tl-range");
  range.max = String(days.length - 1);
  range.value = String(days.length - 1);
  const show = (i) => {
    const d = days[i];
    const n = DATA.tools.filter((t) => t.learned_on && t.learned_on <= d).length;
    const h = [...DATA.history].filter((x) => x.date <= d && x.score).pop();
    $("tl-day").textContent = fmtDate(d);
    $("tl-sum").textContent = T("tl_sum", n, DATA.tools.length, h ? h.score.composite : null);
    if (scene) scene.setDay(d);
  };
  range.addEventListener("input", () => show(+range.value));
  let timer = null;
  const stop = () => { clearInterval(timer); timer = null; $("tl-play").textContent = T("tl_play"); };
  const play = () => {
    if (timer) return stop();
    let i = 0;
    $("tl-play").textContent = T("tl_stop");
    show(i); range.value = "0";
    timer = setInterval(() => {
      i++;
      if (i >= days.length) return stop();
      range.value = String(i); show(i);
    }, days.length > 12 ? 600 : 1300);
  };
  $("tl-play").onclick = play;
  show(days.length - 1);
  return { play: () => { if (!timer && days.length > 1) play(); } };
}

// ── Scène 3D ─────────────────────────────────────────────────────────────────
async function startScene() {
  const host = $("scene");
  const probe = document.createElement("canvas");
  if (!(probe.getContext("webgl2") || probe.getContext("webgl"))) throw new Error("pas de WebGL");
  const THREE = await import("three");
  const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
  const { CSS2DRenderer, CSS2DObject } = await import("three/addons/renderers/CSS2DRenderer.js");
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
  const { RGBELoader } = await import("three/addons/loaders/RGBELoader.js");
  const small = innerWidth < 860;
  let composer = null;
  // La définition de rendu par niveau (voir guessLevel) : 2 en plein écran avec halo saturait
  // les ordinateurs modestes ; 1,5 se distingue à peine sur un écran haute densité.
  const PR = [0.75, 1, 1.25, 1.5];
  let level = guessLevel();
  setQualityClasses(level);

  const renderer = new THREE.WebGLRenderer({ antialias: level >= 3 && !small, alpha: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, PR[level]));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.style.cssText = "position:absolute;inset:0;pointer-events:none";
  host.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x030810);
  scene.fog = new THREE.Fog(0x02040a, 60, 190);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1200);
  camera.position.set(small ? 0 : 10, small ? 18 : 15, small ? 32 : 27);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.enablePan = false;
  // La scène est le fond de la page : la molette et le doigt font défiler, pas zoomer.
  controls.enableZoom = false;
  if (small || TOUCH) { controls.enabled = false; renderer.domElement.style.touchAction = "pan-y"; }
  controls.minDistance = 12; controls.maxDistance = 46; controls.maxPolarAngle = Math.PI * 0.47;
  controls.autoRotate = !REDUCED; controls.autoRotateSpeed = 0.3;
  controls.target.set(small ? 0 : 3, 2.2, 0);

  const CYAN = new THREE.Color(0x5fe3ff), GREEN = new THREE.Color(0x4dffb0), RED = new THREE.Color(0xff5a6e), AMBER = new THREE.Color(0xffb84d);

  // Ciel étoilé réel (Poly Haven, CC0) : fond ET éclairage d'ambiance.
  const pmrem = new THREE.PMREMGenerator(renderer);
  new RGBELoader().load("assets/sky/night_1k.hdr", (hdr) => {
    hdr.mapping = THREE.EquirectangularReflectionMapping;
    scene.environment = pmrem.fromEquirectangular(hdr).texture;
    // Le HDR (luminance médiane mesurée 0,48) n'éclaire plus qu'en douce ; le fond est l'espace.
    scene.environmentIntensity = 0.22;
  });
  scene.add(new THREE.HemisphereLight(0x8fb4ff, 0x05070c, 0.3));
  const tex = new THREE.TextureLoader();
  const sky = tex.load("assets/space/2k_stars_milky_way.jpg", (t) => {
    t.mapping = THREE.EquirectangularReflectionMapping; t.colorSpace = THREE.SRGBColorSpace;
    scene.background = t; scene.backgroundIntensity = 0.85;
  });
  // Le Soleil : une lumière blanche franche, comme sur la Lune (pas d'atmosphère).
  const SUN_DIR = new THREE.Vector3(0.75, 0.32, -0.58).normalize();
  const sunLight = new THREE.DirectionalLight(0xfff4e0, 2.6);
  sunLight.position.copy(SUN_DIR).multiplyScalar(60);
  scene.add(sunLight);
  const sunGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(THREE, "#fff6dc"), color: 0xffffff,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }));
  sunGlow.position.copy(SUN_DIR).multiplyScalar(700); sunGlow.scale.setScalar(160);
  scene.add(sunGlow);
  const coreLight = new THREE.PointLight(0x5fe3ff, 40, 26, 1.6);
  coreLight.position.set(0, 4.2, 0);
  scene.add(coreLight);

  // Sol lunaire.
  const MOON_R = 170;
  // Ce qui est posé suit la courbure de la Lune : le sol descend de r² / 2R au rayon r.
  const drop = (x, z) => (x * x + z * z) / (2 * MOON_R);
  const moonMap = tex.load("assets/space/2k_moon.jpg", (t) => { t.colorSpace = THREE.SRGBColorSpace; });
  moonMap.wrapS = moonMap.wrapT = THREE.RepeatWrapping; moonMap.repeat.set(6, 3); moonMap.anisotropy = 8;
  const ground = new THREE.Mesh(new THREE.SphereGeometry(MOON_R, 160, 96),
    new THREE.MeshStandardMaterial({ map: moonMap, color: 0x9aa0aa, roughness: 0.97, metalness: 0.0 }));
  // Tournée pour que la base se pose sur l'équateur de la texture (pas sur le pôle, où elle se pince).
  ground.rotation.x = Math.PI / 2;
  ground.position.y = -MOON_R + 0.01;
  scene.add(ground);

  // Les planètes, tout autour : elles défilent quand la caméra tourne.
  const planets = [];
  const planet = (file, r, pos, spin, extra = {}) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(r, 64, 48),
      new THREE.MeshStandardMaterial({ map: tex.load(`assets/space/${file}`, (t) => { t.colorSpace = THREE.SRGBColorSpace; }),
        roughness: 1, metalness: 0, fog: false, ...extra }));
    m.position.set(...pos); m.rotation.z = 0.35; scene.add(m);
    planets.push([m, spin]);
    return m;
  };
  // Placées dans la bande de ciel visible : à 15 m au-dessus d'une Lune de rayon 170,
  // l'horizon plonge de ~24° ; une planète doit être entre ~-22° et -4° vue de la caméra.
  const earth = planet("2k_earth_daymap.jpg", 24, [-40, -58, -290], 0.02);       // lever de Terre, droit devant (Apollo 8)
  const clouds = new THREE.Mesh(new THREE.SphereGeometry(24.35, 64, 48),
    new THREE.MeshStandardMaterial({ map: tex.load("assets/space/2k_earth_clouds.jpg"), transparent: true, opacity: 0.55,
      depthWrite: false, fog: false }));
  earth.add(clouds); planets.push([clouds, 0.028]);
  earth.add(atmosphere(THREE, 25.4, 0x6fb4ff));
  planet("2k_mars.jpg", 7, [-280, -28, 90], 0.03);
  const saturn = planet("2k_saturn.jpg", 20, [290, -38, 60], 0.025);
  const ringGeo = new THREE.RingGeometry(26, 46, 128);
  const rp = ringGeo.attributes.position, uv = ringGeo.attributes.uv, v3 = new THREE.Vector3();
  for (let i = 0; i < rp.count; i++) { v3.fromBufferAttribute(rp, i); uv.setXY(i, (v3.length() - 26) / 20, 0.5); }
  const ring = new THREE.Mesh(ringGeo, new THREE.MeshBasicMaterial({ map: tex.load("assets/space/2k_saturn_ring_alpha.png"),
    side: THREE.DoubleSide, transparent: true, opacity: 0.9, fog: false, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2.4; saturn.add(ring);
  planet("2k_jupiter.jpg", 34, [120, -60, 370], 0.04);
  planet("2k_neptune.jpg", 9, [-200, -42, 260], 0.03);
  const grid = new THREE.PolarGridHelper(13, 24, 6, 96, 0x2a7d9c, 0x123446);
  grid.position.y = 0.02; grid.material.transparent = true; grid.material.opacity = 0.45; scene.add(grid);

  // Modèles Kenney (Space Kit, CC0) : chargés une fois, clonés ensuite.
  const loader = new GLTFLoader();
  const names = ["platform_large", "hangar_largeA", "hangar_roundGlass", "hangar_smallA", "satelliteDish_large",
    "satelliteDish", "machine_wireless", "machine_generatorLarge", "machine_generator", "desk_computer",
    "structure_detailed", "rocket_baseA", "rocket_fuelA", "rocket_topA", "rover", "craft_speederA", "crater",
    "craterLarge", "rock_largeA", "rocks_smallA", "rock_crystals", "rock_crystalsLargeA", "machine_barrel",
    "supports_low", "astronautA", "meteor"];
  const M = {};
  await Promise.all(names.map((n) => loader.loadAsync(`assets/models/${n}.glb`).then((g) => (M[n] = g.scene)).catch(() => null)));
  // Les modèles Kenney ont leur origine dans un coin (ce sont des tuiles) : chacun est
  // recentré sur le milieu de sa base avant d'être posé, tourné et mis à l'échelle.
  const box = new THREE.Box3(), mid = new THREE.Vector3();
  const place = (name, x, z, s = 1, rotY = 0, y = 0) => {
    if (!M[name]) return null;
    const inner = M[name].clone(true);
    box.setFromObject(inner); box.getCenter(mid);
    inner.position.set(-mid.x, -box.min.y, -mid.z);
    const o = new THREE.Group(); o.add(inner);
    o.scale.setScalar(s); o.position.set(x, y - drop(x, z), z); o.rotation.y = rotY;
    scene.add(o);
    return o;
  };
  const dim = (obj, f) => obj && obj.traverse((m) => {
    if (m.isMesh) { m.material = m.material.clone(); m.material.color.multiplyScalar(f); }
  });

  const GREY = new THREE.Color(0x8d929b);
  const lunar = (obj) => obj && obj.traverse((m) => {
    if (m.isMesh) { m.material = m.material.clone(); m.material.color.lerp(GREY, 0.75); }
  });
  // Paysage : cratères, rochers, cristaux, en dehors du cercle de la base (posés sur la courbure).
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 26; i++) {
    const a = rnd() * Math.PI * 2, r = 16 + rnd() * 22;
    const kind = ["crater", "craterLarge", "rock_largeA", "rocks_smallA", "rock_crystals", "rock_crystalsLargeA", "meteor"][i % 7];
    lunar(place(kind, Math.cos(a) * r, Math.sin(a) * r, 1.6 + rnd() * 2.4, rnd() * 6.28));
  }

  // Le centre : la plateforme, Matthew au-dessus.
  place("platform_large", 0, 0, 3.2);
  place("astronautA", 3.4, 2.6, 1.4, -0.6, 0.2);
  const fresnel = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uColor: { value: CYAN.clone() }, uPulse: { value: 0 } },
    vertexShader: `varying vec3 vN; varying vec3 vV; void main(){ vec4 mv = modelViewMatrix*vec4(position,1.); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }`,
    fragmentShader: `uniform float uTime; uniform vec3 uColor; uniform float uPulse; varying vec3 vN; varying vec3 vV;
      void main(){ float f = pow(1.-max(dot(vN,vV),0.), 2.2); float scan = .55+.45*sin(gl_FragCoord.y*.9+uTime*4.);
        gl_FragColor = vec4(uColor*(f*1.8+.12+uPulse*.6)*mix(.8,1.,scan), f*.95+.18+uPulse*.3); }`,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const CORE_Y = 4.2;
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.3, 5), fresnel);
  core.position.y = CORE_Y; scene.add(core);
  const shell = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.95, 1)),
    new THREE.LineBasicMaterial({ color: 0x5fe3ff, transparent: true, opacity: 0.4 }));
  shell.position.y = CORE_Y; scene.add(shell);
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.5, CORE_Y, 24, 1, true),
    new THREE.MeshBasicMaterial({ color: 0x5fe3ff, transparent: true, opacity: 0.18, blending: THREE.AdditiveBlending, depthWrite: false }));
  beam.position.y = CORE_Y / 2; scene.add(beam);
  const coreLbl = document.createElement("div"); coreLbl.className = "lbl-domain"; coreLbl.textContent = "Matthew";
  const coreObj = new CSS2DObject(coreLbl); coreObj.position.set(0, CORE_Y + 2.6, 0); scene.add(coreObj);

  // Un bâtiment par domaine, un module par outil, une balise par module.
  const BUILDING = { "Système": ["hangar_largeA", 1.5], "Bureau": ["hangar_smallA", 1.8], "Infos": ["satelliteDish_large", 2.0],
    "Communication": ["machine_wireless", 2.2], "Mémoire": ["hangar_roundGlass", 1.9], "Code": ["rocket", 1.8],
    "Noyau": ["machine_generatorLarge", 2.0], "Autres": ["structure_detailed", 1.4] };
  const POD = { "Système": "machine_generator", "Bureau": "desk_computer", "Infos": "satelliteDish", "Communication": "machine_wireless",
    "Mémoire": "machine_barrel", "Code": "structure_detailed", "Noyau": "supports_low", "Autres": "rocks_smallA" };
  const spinners = [];
  const stations = new Map(), doms = DATA.domains.filter((d) => DATA.tools.some((t) => t.domain === d));
  const sector = (Math.PI * 2) / doms.length;
  const beaconGeo = new THREE.SphereGeometry(0.2, 16, 16);
  const hits = [];
  doms.forEach((dname, di) => {
    const tools = DATA.tools.filter((t) => t.domain === dname);
    const a0 = di * sector + 0.14, a1 = (di + 1) * sector - 0.14, mid = (a0 + a1) / 2;
    const bx = Math.cos(mid) * 12.2, bz = Math.sin(mid) * 12.2, face = -mid + Math.PI / 2;
    const [bName, bScale] = BUILDING[dname] || BUILDING["Autres"];
    if (bName === "rocket") {
      place("rocket_baseA", bx, bz, bScale); place("rocket_fuelA", bx, bz, bScale, 0, bScale); place("rocket_topA", bx, bz, bScale, 0, bScale * 2);
    } else {
      const b = place(bName, bx, bz, bScale, face);
      if (b && /satellite|wireless/.test(bName)) spinners.push(b);
    }
    const dl = document.createElement("div"); dl.className = "lbl-domain pick"; dl.textContent = dom(dname);
    // Survol cinématique : un clic sur un domaine fait voler la caméra jusqu'à son bâtiment.
    dl.addEventListener("click", () => flyTo(new THREE.Vector3(bx, 1.5 - drop(bx, bz), bz), 13));
    const dObj = new CSS2DObject(dl); dObj.position.set(bx, bName === "rocket" ? 7.4 : 4.6, bz); scene.add(dObj);
    tools.forEach((t, i) => {
      const a = a0 + ((i + 0.5) * (a1 - a0)) / tools.length, r = 6.6 + (i % 2) * 2.2;
      const x = Math.cos(a) * r, z = Math.sin(a) * r;
      const learned = t.examples > 0;
      const pod = place(POD[dname] || "rocks_smallA", x, z, 1.15, -a + Math.PI / 2);
      if (!learned) dim(pod, 0.3);
      const onCol = t.accuracy == null ? CYAN.clone() : AMBER.clone().lerp(GREEN, t.accuracy);
      const offCol = new THREE.Color(0x33475a);
      const col = learned ? onCol : offCol;
      const beacon = new THREE.Mesh(beaconGeo, new THREE.MeshBasicMaterial({ color: col.clone().multiplyScalar(learned ? 2.2 : 0.8) }));
      beacon.position.set(x, 2.1 - drop(x, z), z);
      scene.add(beacon);
      const el = document.createElement("div"); el.className = "lbl-tool" + (learned ? "" : " off"); el.textContent = label(t);
      const lo = new CSS2DObject(el); lo.position.set(x, 2.65 - drop(x, z), z); scene.add(lo);
      const hit = new THREE.Mesh(new THREE.SphereGeometry(0.75, 8, 8), new THREE.MeshBasicMaterial({ visible: false }));
      hit.position.copy(beacon.position); hit.userData.tool = t.id; scene.add(hit); hits.push(hit);
      stations.set(t.id, { mesh: beacon, el, base: beacon.material.color.clone(), learned_on: t.learned_on,
                           on: onCol.clone().multiplyScalar(2.2), off: offCol.clone().multiplyScalar(0.8) });
    });
  });

  // La vie de la base : un rover qui patrouille, un vaisseau en orbite.
  const rover = place("rover", 15, 0, 1.6);
  const ship = place("craft_speederA", 0, 20, 1.8, 0, 9);

  // Halo (bloom) au niveau 3 seulement : seules les lumières vives brillent. C'est la passe la
  // plus chère (plusieurs flous plein écran par image) ; chargée à la demande.
  let composerLoading = null;
  function ensureComposer() {
    if (composer || composerLoading || small || REDUCED) return;
    composerLoading = (async () => {
      try {
        const { EffectComposer } = await import("three/addons/postprocessing/EffectComposer.js");
        const { RenderPass } = await import("three/addons/postprocessing/RenderPass.js");
        const { UnrealBloomPass } = await import("three/addons/postprocessing/UnrealBloomPass.js");
        const { OutputPass } = await import("three/addons/postprocessing/OutputPass.js");
        const c = new EffectComposer(renderer);
        c.addPass(new RenderPass(scene, camera));
        c.addPass(new UnrealBloomPass(new THREE.Vector2(256, 256), 0.8, 0.45, 0.9));
        c.addPass(new OutputPass());
        c.setSize(host.clientWidth, host.clientHeight);
        composer = c;
      } catch (e) { composer = null; }
    })();
  }
  if (level >= 3) await (ensureComposer(), composerLoading);

  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setSize(w, h); labels.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    if (composer) { composer.setPixelRatio(renderer.getPixelRatio()); composer.setSize(w, h); }
  }
  new ResizeObserver(resize).observe(host); resize();
  function applyLevel(lv) {
    level = lv;
    renderer.setPixelRatio(Math.min(devicePixelRatio, PR[level]));
    resize();
    setQualityClasses(level);
    if (level >= 3) ensureComposer();
  }

  // Faisceaux : un point lumineux voyage du noyau à la balise, sur un arc.
  const beams = [];
  const ORIGIN = new THREE.Vector3(0, CORE_Y, 0);
  function fire(targetId, ok, expectedId) {
    const st = stations.get(targetId);
    const end = st ? st.mesh.position.clone() : new THREE.Vector3(0, CORE_Y + 6, 0);
    const curve = new THREE.QuadraticBezierCurve3(ORIGIN.clone(), ORIGIN.clone().lerp(end, 0.5).add(new THREE.Vector3(0, 4, 0)), end);
    const color = ok ? GREEN : RED;
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(48)),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0 }));
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.16, 12, 12), new THREE.MeshBasicMaterial({ color: color.clone().multiplyScalar(3) }));
    scene.add(line, dot);
    beams.push({ curve, line, dot, t: 0, st, ok, color });
    if (!ok && expectedId && stations.get(expectedId)) {             // l'outil attendu, en pointillé
      const ex = stations.get(expectedId).mesh.position;
      const dash = new THREE.Line(new THREE.BufferGeometry().setFromPoints([ORIGIN.clone(), ex.clone()]),
        new THREE.LineDashedMaterial({ color: 0xffb84d, dashSize: 0.3, gapSize: 0.2, transparent: true, opacity: 0.7 }));
      dash.computeLineDistances(); scene.add(dash);
      setTimeout(() => scene.remove(dash), 3200);
    }
    fresnel.uniforms.uPulse.value = 1;
  }
  function focus(id) {
    for (const [k, s] of stations) s.el.classList.toggle("on", k === id);
  }

  // Cliquer une balise ouvre la fiche de l'outil (un glissement de caméra n'est pas un clic).
  let pickCb = null, down = null;
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  renderer.domElement.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
    pickAt(e.clientX, e.clientY);
  });
  function pickAt(x, y) {
    for (const el of host.querySelectorAll(".lbl-domain.pick")) {
      const b = el.getBoundingClientRect();
      if (x >= b.left - 8 && x <= b.right + 8 && y >= b.top - 8 && y <= b.bottom + 8) { el.click(); return; }
    }
    if (!pickCb) return;
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const got = ray.intersectObjects(hits, false)[0];
    if (got) pickCb(got.object.userData.tool);
  }
  // Tactile : un « click » n'arrive qu'après un toucher court (pas après un défilement).
  if (TOUCH) addEventListener("click", (e) => {
    if (e.target.closest("a, button, input, select, label, .card:not(.bare), .details, .drawer, .langbtn")) return;
    pickAt(e.clientX, e.clientY);
  });
  const training = !!DATA.live;
  // Survol : la caméra et sa cible glissent ensemble (1,6 s, accélération douce) ; la rotation
  // automatique reprend 12 s après.
  let fly = null, resumeAt = 0;
  function flyTo(target, dist, cb) {
    const dir = camera.position.clone().sub(controls.target).setY(0).normalize();
    const p1 = target.clone().add(dir.multiplyScalar(dist)).add(new THREE.Vector3(0, dist * 0.55, 0));
    fly = { t: 0, p0: camera.position.clone(), p1, t0: controls.target.clone(), t1: target.clone(), cb };
    controls.autoRotate = false;
  }
  // Un plan par chapitre du récit : [position de la caméra, cible, rotation automatique].
  const SHOTS = {
    overview: [[10, 15, 27], [3, 2.2, 0], true], base: [[0, 9, 17], [0, 2.4, 0], true],
    domains: [[-17, 7, 11], [0, 1, 0], true], night: [[0.5, 34, 8], [0, 0, 0], false],
    exam: [[7, 6.5, 9.5], [0, 3.6, 0], false], progress: [[4, 4.5, 24], [-24, 6, -140], false],
    far: [[0, 22, 46], [0, 0, 0], true],
  };
  function goShot(name) {
    const sh = SHOTS[name];
    if (!sh || OG) return;
    const k = small ? 1.35 : 1;
    const p1 = new THREE.Vector3(...sh[0]).multiplyScalar(k);
    fly = { t: 0, p0: camera.position.clone(), p1, t0: controls.target.clone(), t1: new THREE.Vector3(...sh[1]),
            cb: null, rotate: sh[2] && !REDUCED };
    controls.autoRotate = false;
  }
  function setDay(d) {
    for (const s of stations.values()) {
      const lit = !!(s.learned_on && s.learned_on <= d);
      s.base = (lit ? s.on : s.off).clone();
      s.el.classList.toggle("off", !lit);
    }
  }
  if (OG) controls.autoRotate = false;
  const clock = new THREE.Clock(), ONE = new THREE.Vector3(1, 1, 1);
  // Tactile : pendant que le doigt fait défiler, une image sur trois seulement — le défilement
  // passe avant la 3D (un GPU de téléphone saturé fait saccader la page entière).
  let scrolling = 0, frame = 0;
  if (TOUCH) addEventListener("scroll", () => (scrolling = performance.now()), { passive: true });
  // Les détails (fond plein) recouvrent la scène : inutile de la dessiner sous eux.
  const details = $("details");
  let offstage = false;
  const checkStage = () => { offstage = details.getBoundingClientRect().top < -200; };
  addEventListener("scroll", checkStage, { passive: true }); checkStage();
  // Adaptatif (qualité « auto ») : toutes les 90 images, si l'intervalle moyen dépasse le budget
  // du niveau (24 ms, soit moins de ~40 i/s ; 48 ms quand on vise 30 i/s), on descend d'un niveau.
  let last = performance.now(), sum = 0, count = 0, settleUntil = last + 3000, warned = false;
  function adapt(now) {
    const gap = now - last;
    last = now;
    if (QUALITY !== "auto" || document.hidden || gap > 250 || now < settleUntil) { sum = count = 0; return; }
    sum += gap; count++;
    if (count < 90) return;
    const avg = sum / count;
    sum = count = 0;
    if (avg > (level <= 1 ? 48 : 24) && level > 0) {
      applyLevel(level - 1);
      settleUntil = now + 2500;
      if (!warned && level <= 1 && $("guide").hidden) { warned = true; toast(T("q_toast")); }
    }
  }
  (function loop(now = performance.now()) {
    if (TOUCH && performance.now() - scrolling < 200 && ++frame % 3) { requestAnimationFrame(loop); return; }
    if (offstage && !fly) { last = now; requestAnimationFrame(loop); return; }
    if (level <= 1 && now - last < 31) { requestAnimationFrame(loop); return; }   // 30 images/s
    adapt(now);
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    fresnel.uniforms.uTime.value = t;
    fresnel.uniforms.uPulse.value *= 0.94;
    if (training) fresnel.uniforms.uPulse.value = Math.max(fresnel.uniforms.uPulse.value, 0.35 + 0.35 * Math.sin(t * 6));
    coreLight.intensity = 40 + fresnel.uniforms.uPulse.value * 60;
    if (!REDUCED) {
      shell.rotation.y += dt * 0.15; shell.rotation.x += dt * 0.05;
      core.position.y = CORE_Y + Math.sin(t * 1.2) * 0.12;
      for (const s of spinners) s.rotation.y += dt * 0.25;
      for (const [m, sp] of planets) m.rotation.y += dt * sp;
      // Les modèles Kenney regardent vers −Z : sur le cercle (cos a, sin a), la marche avant
      // (−sin a, cos a) demande rotation.y = π − a. Le vaisseau tourne dans le même sens que la
      // caméra (rotation automatique) et que le rover : il y volait à reculons et à contre-sens.
      if (rover) { const a = t * 0.08; rover.position.set(Math.cos(a) * 15.5, -drop(15.5, 0), Math.sin(a) * 15.5); rover.rotation.y = Math.PI - a; }
      if (ship) { const a = t * 0.15; ship.position.set(Math.cos(a) * 21, 9 + Math.sin(t * 0.7) * 0.8, Math.sin(a) * 21); ship.rotation.y = Math.PI - a; }
    }
    for (const s of stations.values()) { s.mesh.material.color.lerp(s.base, 0.04); s.mesh.scale.lerp(ONE, 0.08); }
    for (let i = beams.length - 1; i >= 0; i--) {
      const b = beams[i];
      b.t += dt * (REDUCED ? 3 : 0.9);
      const k = Math.min(b.t, 1);
      b.dot.position.copy(b.curve.getPoint(k));
      b.line.material.opacity = Math.min(0.9, b.t * 1.5) * (b.t > 1 ? Math.max(0, 2.2 - b.t) : 1);
      if (b.t >= 1 && !b.hit) {
        b.hit = true; scene.remove(b.dot);
        if (b.st) { b.st.mesh.material.color.copy(b.color).multiplyScalar(4); b.st.mesh.scale.setScalar(2.4); }
      }
      if (b.t > 2.2) { scene.remove(b.line); beams.splice(i, 1); }
    }
    if (fly) {
      fly.t = Math.min(1, fly.t + dt / 1.6);
      const e = fly.t < 0.5 ? 4 * fly.t ** 3 : 1 - Math.pow(-2 * fly.t + 2, 3) / 2;
      camera.position.lerpVectors(fly.p0, fly.p1, e);
      controls.target.lerpVectors(fly.t0, fly.t1, e);
      if (fly.t >= 1) {
        const cb = fly.cb, rot = fly.rotate;
        fly = null;
        if (rot === undefined) resumeAt = t + 12; else controls.autoRotate = rot;
        if (cb) cb();
      }
    } else if (resumeAt && t > resumeAt && !REDUCED && !OG) { controls.autoRotate = true; resumeAt = 0; }
    controls.update();
    composer && level >= 3 ? composer.render() : renderer.render(scene, camera);
    labels.render(scene, camera);
    requestAnimationFrame(loop);
  })();

  return {
    fire, focus, setDay, goShot, onPick: (cb) => (pickCb = cb),
    setQuality: () => { applyLevel(guessLevel()); settleUntil = performance.now() + 3000; },
    flyToTool: (id, cb) => { const s = stations.get(id); if (s) flyTo(s.mesh.position.clone(), 9, cb); else if (cb) cb(); },
  };
}

function glowTexture(THREE, color) {
  const c = document.createElement("canvas"); c.width = c.height = 128;
  const g = c.getContext("2d"), grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, color); grd.addColorStop(0.2, color + "aa"); grd.addColorStop(1, "transparent");
  g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function atmosphere(THREE, r, color) {
  return new THREE.Mesh(new THREE.SphereGeometry(r, 64, 48), new THREE.ShaderMaterial({
    uniforms: { uColor: { value: new THREE.Color(color) } },
    vertexShader: `varying vec3 vN; varying vec3 vV; void main(){ vec4 mv = modelViewMatrix*vec4(position,1.); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }`,
    fragmentShader: `uniform vec3 uColor; varying vec3 vN; varying vec3 vV; void main(){ float f = pow(1.-max(dot(vN,vV),0.), 3.); gl_FragColor = vec4(uColor, f*.9); }`,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.BackSide,
  }));
}

// ── Rejeu de l'examen ────────────────────────────────────────────────────────
function startReplay(scene) {
  const days = examDays();
  if (!days.length || !DATA.showcase.length) { $("r-req").textContent = T("first_exam"); return; }
  const day = days[days.length - 1];
  const L = DATA.showcase.filter((x) => x.results[day] && !x.target.startsWith("__"));
  if (!L.length) { $("r-req").textContent = T("first_exam"); return; }
  const ok = L.filter((x) => x.results[day].ok).length;
  let idx = 0;
  function step() {
    const s = L[idx % L.length], r = s.results[day];
    $("r-req").textContent = `« ${s.request} »`;
    $("r-chosen").textContent = r.chosen ? toolName(r.chosen) : T("no_tool");
    const b = $("r-badge"); b.textContent = r.ok ? T("ok") : T("ko"); b.className = "badge" + (r.ok ? "" : " ko");
    $("r-seen").textContent = s.heldout ? T("never_seen") : T("seen_train");
    $("r-count").textContent = T("count", (idx % L.length) + 1, L.length, ok, fmtDate(day), r.ms ? (r.ms / 1000).toFixed(1) : "");
    if (scene) { scene.focus(r.chosen); scene.fire(r.chosen, r.ok, s.target); }
  }
  step();
  if (!OG) setInterval(() => { if (!document.hidden) { idx++; step(); } }, REDUCED ? 7000 : 4200);
}

// ── Le guide : Matthew présente la page lui-même, façon tutoriel de jeu vidéo ──────────
// Un robot (« RobotExpressive », Tomás Laulhé, CC0) dans un petit canevas à part : il salue,
// acquiesce, lève le pouce, danse ; sa bouche bouge pendant qu'il « parle » (texte tapé lettre à
// lettre, voix du navigateur si on l'active). Chaque étape peut éclairer un élément de la page et
// déplacer la caméra de la scène. Vu une fois → plus jamais automatique ; « ? » le rappelle.
let GUIDE = null;

function guideSteps() {
  const s = DATA.champion && DATA.champion.score && DATA.champion.score.composite;
  const night = DATA.live ? { say: G("night_live"), anim: "Jump", target: "#livebox", shot: "night" }
    : !$("next").hidden && !$("next").classList.contains("waiting") ? { say: G("night"), target: "#next", shot: "night" }
    : { say: G("night_plain"), shot: "night" };
  return [
    { say: G("hello"), anim: "Wave", shot: "overview" },
    { say: G("base"), anim: "Yes", shot: "base" },
    { say: G("skills"), shot: "domains" },
    night,
    { say: G("exam"), anim: "Yes", shot: "exam" },
    s != null ? { say: G("score", pct(s)), anim: "ThumbsUp", target: ".hero-num", shot: "overview" } : null,
    Array.isArray(DATA.specialties) && DATA.specialties.length ? { say: G("spec"), shot: "domains" } : null,
    { say: G("bye"), anim: "Dance", shot: "far", end: true },
  ].filter(Boolean);
}

async function guideBot(host) {
  const THREE = await import("three");
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
  camera.position.set(0, 2.9, 12.5); camera.lookAt(0, 2.3, 0);
  scene.add(new THREE.HemisphereLight(0xcfe9ff, 0x0a1426, 2.2));
  const key = new THREE.DirectionalLight(0xffffff, 2.4); key.position.set(3, 6, 6); scene.add(key);
  const rim = new THREE.DirectionalLight(0x5fe3ff, 3); rim.position.set(-4, 3, -5); scene.add(rim);
  const gltf = await new GLTFLoader().loadAsync("assets/models/matthew_guide.glb");
  const bot = gltf.scene;
  // Aux couleurs de Matthew : corps cyan, membres acier (l'original est orange et gris).
  bot.traverse((m) => {
    if (!m.isMesh) return;
    const name = m.material.name;
    m.material = m.material.clone();
    if (name === "Main") { m.material.color.set(0x23b5e3); m.material.emissive = new THREE.Color(0x0b4a63); }
    if (name === "Grey") m.material.color.set(0xb9c8d4);
  });
  scene.add(bot);
  const head = [];
  bot.traverse((m) => { if (m.morphTargetDictionary && "Surprised" in m.morphTargetDictionary) head.push(m); });
  const mixer = new THREE.AnimationMixer(bot);
  const clips = Object.fromEntries(gltf.animations.map((c) => [c.name, c]));
  const LOOPS = new Set(["Idle", "Dance", "Walking"]);
  let current = mixer.clipAction(clips.Idle);
  current.play();
  mixer.addEventListener("finished", () => play("Idle"));
  function play(name) {
    const clip = clips[name];
    if (!clip) return;
    const next = mixer.clipAction(clip);
    if (next === current) return;
    next.reset();
    if (!LOOPS.has(name)) { next.setLoop(THREE.LoopOnce, 1); next.clampWhenFinished = true; }
    next.play();
    current.crossFadeTo(next, 0.3, false);
    current = next;
  }
  let talking = false, running = false, raf = 0;
  const clock = new THREE.Clock();
  function size() {
    const w = host.clientWidth || 1, h = host.clientHeight || 1;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    // Il se tourne vers sa bulle : à gauche sur ordinateur, à droite sur téléphone.
    const bubble = document.querySelector(".g-bubble").getBoundingClientRect(), me = host.getBoundingClientRect();
    bot.rotation.y = bubble.left + bubble.width / 2 < me.left + me.width / 2 ? -0.45 : 0.45;
  }
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    if (!REDUCED) mixer.update(dt);
    const mouth = talking && !REDUCED ? 0.25 + 0.25 * Math.sin(t * 22) * Math.sin(t * 7.3) : 0;
    for (const m of head) m.morphTargetInfluences[m.morphTargetDictionary.Surprised] = Math.max(0, mouth);
    renderer.render(scene, camera);
    if (running) raf = requestAnimationFrame(frame);
  }
  return {
    play, size,
    talk: (on) => (talking = on),
    start: () => { size(); clock.getDelta(); if (!running) { running = true; frame(); } },
    stop: () => { running = false; cancelAnimationFrame(raf); },
  };
}

function guideVoice() {
  const ok = "speechSynthesis" in window;
  let on = ok && store.get("matthew-voice") === "1";
  const btn = $("g-voice");
  btn.hidden = !ok;
  const paint = () => { btn.textContent = on ? "🔊" : "🔇"; btn.setAttribute("aria-pressed", String(on)); };
  paint();
  let pick = null;
  const choose = () => {
    const vs = speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith(LANG));
    pick = vs.find((v) => /neural|natural|online/i.test(v.name)) || vs[0] || null;
  };
  if (ok) { choose(); speechSynthesis.onvoiceschanged = choose; }
  return {
    toggle: (text) => { on = !on; store.set("matthew-voice", on ? "1" : "0"); paint(); if (on) say(text); else speechSynthesis.cancel(); },
    say,
    stop: () => ok && speechSynthesis.cancel(),
  };
  function say(text) {
    if (!on) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/[«»"]/g, ""));
    u.lang = LANG === "fr" ? "fr-FR" : "en-GB"; if (pick) u.voice = pick;
    u.rate = 1.05; u.pitch = 1.15;
    speechSynthesis.speak(u);
  }
}

async function startGuide(scene) {
  if (!DATA) return;
  const box = $("guide"), text = $("g-text"), next = $("g-next"), spotEl = $("g-spot");
  if (!GUIDE) {
    GUIDE = { voice: guideVoice(), bot: null };
    guideBot($("g-bot")).then((b) => { GUIDE.bot = b; if (!box.hidden) { b.start(); b.play(GUIDE.anim || "Wave"); } })
      .catch(() => $("g-bot").classList.add("flat"));     // pas de WebGL : un noyau lumineux le remplace
  }
  const steps = guideSteps();
  let i = -1, typing = null, full = "";
  scrollTo({ top: 0, behavior: "instant" });
  box.hidden = false;
  document.body.classList.add("touring");
  if (GUIDE.bot) GUIDE.bot.start();

  function spot(sel) {
    const el = sel && document.querySelector(sel);
    if (!el || el.hidden || !el.getClientRects().length) {
      spotEl.className = "g-spot none";
      Object.assign(spotEl.style, { left: "50%", top: "50%", width: "0px", height: "0px" });
      return;
    }
    const r = el.getBoundingClientRect(), pad = 10;
    spotEl.className = "g-spot on";
    Object.assign(spotEl.style, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px", height: r.height + 2 * pad + "px" });
  }
  const respot = () => i >= 0 && i < steps.length && spot(steps[i].target);
  addEventListener("resize", respot);
  addEventListener("scroll", respot, { passive: true });

  function type(str) {
    clearInterval(typing);
    full = str;
    if (GUIDE.bot) GUIDE.bot.talk(true);
    if (REDUCED) { text.textContent = str; if (GUIDE.bot) GUIDE.bot.talk(false); return; }
    let n = 0;
    text.innerHTML = '<span></span><i class="caret"></i>';
    const span = text.firstChild;
    typing = setInterval(() => {
      n += str[n] === " " ? 2 : 1;
      span.textContent = str.slice(0, n);
      if (n >= str.length) finishTyping();
    }, 26);
  }
  function finishTyping() {
    clearInterval(typing); typing = null;
    text.textContent = full;
    if (GUIDE.bot) GUIDE.bot.talk(false);
  }
  function go(k) {
    i = k;
    if (i >= steps.length) return close();
    const st = steps[i];
    $("g-step").textContent = `${i + 1}/${steps.length}`;
    next.textContent = st.end ? T("g_end") : T("g_next");
    GUIDE.anim = st.anim || "Idle";
    if (GUIDE.bot) GUIDE.bot.play(GUIDE.anim);
    if (scene && st.shot) scene.goShot(st.shot);
    spot(st.target);
    type(st.say);
    GUIDE.voice.say(st.say);
  }
  function close() {
    clearInterval(typing);
    box.hidden = true;
    document.body.classList.remove("touring");
    GUIDE.voice.stop();
    if (GUIDE.bot) GUIDE.bot.stop();
    store.set("matthew-tour", "1");
    removeEventListener("resize", respot);
    removeEventListener("scroll", respot);
    removeEventListener("keydown", keys, true);
    if (scene) scene.goShot("overview");
    $("tour-btn").focus({ preventScroll: true });
  }
  function advance() { if (typing) finishTyping(); else go(i + 1); }
  function keys(e) {
    if (box.hidden) return;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if ((e.key === "Enter" || e.key === "ArrowRight") && !e.target.closest(".g-actions button:not(#g-next)")) { e.preventDefault(); advance(); }
  }
  next.onclick = advance;
  $("g-skip").onclick = close;
  $("g-voice").onclick = () => GUIDE.voice.toggle(full);
  addEventListener("keydown", keys, true);
  go(0);
  next.focus({ preventScroll: true });
}

main();
