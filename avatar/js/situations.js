/**
 * situations.js — des moments ordinaires, ecrits une fois pour le labo ET le test.
 *
 * POURQUOI CE FICHIER
 *   Un scenario de labo que rien ne verifie peut montrer n'importe quoi : on le
 *   regarde, on trouve que ca a l'air bien, et personne ne saura qu'il a cesse
 *   de faire ce qu'il dit. Ces situations sont donc des DONNEES : le labo les
 *   joue a l'ecran, `avatar/checks/situations.mjs` les joue sous Node et
 *   verifie, pour chacune, le signal qu'elle annonce — sur la sortie.
 *
 * CE QU'UNE SITUATION CONTIENT
 *   Ce qui arrive, dans l'ordre, et rien de ce que le visage doit en faire :
 *     { t, state }         l'etat machine change (ce que main.py dirait)
 *     { t, ask }           JARVIS appelle `set_presence(...)` — une demande
 *     { t, say: [a, b] }   JARVIS parle pendant cette duree
 *     { t, hear: [a, b] }  l'utilisateur parle (son micro), avec des pauses
 *   `expect` dit, en mots, ce qu'un observateur devrait percevoir. Le test en
 *   fait une mesure ; le labo l'affiche.
 *
 * LE MOTEUR N'Y EST NOMME NULLE PART
 *   Aucune forme, aucun os, aucun accent : ce que JARVIS demande, comme il le
 *   demanderait. Le reste est le travail du moteur, et c'est lui qu'on juge.
 */

export const SITUATIONS = {
  'quelqu\'un arrive': {
    situation: 'le mot d\'eveil, puis bonjour',
    expect: 'il a vu quelqu\'un : les yeux s\'ouvrent, les sourcils saluent, le regard vient sur lui',
    steps: [
      { t: 0.0, state: 'WAKING' },
      { t: 1.3, state: 'LISTENING' },
      { t: 1.5, ask: { intent: 'greet' } },
      { t: 1.6, say: [1.6, 2.6] },
    ],
  },
  'resultat inattendu': {
    situation: 'une recherche renvoie quelque chose d\'etonnant',
    expect: 'il a vu quelque chose : surprise vive, pas de clignement pendant, puis il revient',
    steps: [
      { t: 0.0, state: 'THINKING' },
      { t: 0.4, ask: { intent: 'investigate' } },
      { t: 1.6, ask: { expression: 'surprised', intensity: 0.7, reason: 'resultat inattendu' } },
      { t: 2.6, state: 'SPEAKING' },
      { t: 2.7, say: [2.7, 4.5] },
    ],
  },
  'besoin d\'une confirmation': {
    situation: 'une action irreversible attend un oui',
    expect: 'il attend votre reponse : visage serieux, regard tenu, rien ne le detourne',
    steps: [
      { t: 0.0, state: 'SPEAKING' },
      { t: 0.1, ask: { intent: 'confirm' } },
      { t: 0.2, say: [0.2, 2.2] },
      { t: 2.4, state: 'CONFIRM' },
    ],
  },
  'avertissement': {
    situation: 'un risque vient d\'apparaitre',
    expect: 'il veut attirer votre attention : inquietude franche, regard stable sur vous',
    steps: [
      { t: 0.0, state: 'SPEAKING' },
      { t: 0.05, ask: { intent: 'warn' } },
      { t: 0.15, say: [0.15, 3.0] },
    ],
  },
  'l\'utilisateur remercie': {
    situation: '« merci ! »',
    expect: 'il a compris : un petit signe de tete, chaleureux, sans ceremonie',
    steps: [
      { t: 0.0, state: 'LISTENING' },
      { t: 0.2, hear: [0.2, 1.1] },
      { t: 1.4, state: 'THINKING' },
      { t: 1.9, state: 'SPEAKING' },
      { t: 1.95, ask: { intent: 'acknowledge', valence: 0.5 } },
      { t: 2.0, say: [2.0, 2.8] },
    ],
  },
  'l\'utilisateur plaisante': {
    situation: 'une blague',
    expect: 'il est legerement amuse : un demi-sourire qui arrive apres les yeux',
    steps: [
      { t: 0.0, state: 'LISTENING' },
      { t: 0.2, hear: [0.2, 2.2] },
      { t: 2.4, state: 'THINKING' },
      { t: 2.9, state: 'SPEAKING' },
      { t: 2.95, ask: { intent: 'amuse' } },
      { t: 3.1, say: [3.1, 4.8] },
    ],
  },
  'JARVIS reflechit': {
    situation: 'une question qui demande un calcul',
    expect: 'il reflechit : le regard s\'echappe, le front travaille, la tete penche',
    steps: [
      { t: 0.0, state: 'LISTENING' },
      { t: 0.2, hear: [0.2, 2.0] },
      { t: 2.2, state: 'THINKING' },
      { t: 2.3, ask: { intent: 'think' } },
    ],
  },
  'JARVIS est interrompu': {
    situation: 'l\'utilisateur lui coupe la parole',
    expect: 'il s\'arrete et ecoute : la bouche se ferme sans claquer, le regard revient',
    steps: [
      { t: 0.0, state: 'SPEAKING' },
      { t: 0.1, ask: { intent: 'explain' } },
      // Coupe a 1.15 s : au sommet d'une syllabe appuyee, bouche ouverte.
      { t: 0.2, say: [0.2, 1.15] },
      { t: 1.15, state: 'LISTENING' },
      { t: 1.25, hear: [1.25, 3.2] },
    ],
  },
  'tache reussie': {
    situation: 'le fichier est exporte',
    expect: 'il est satisfait : le menton se releve, un sourire retenu',
    steps: [
      { t: 0.0, state: 'SPEAKING' },
      { t: 0.05, ask: { intent: 'report_success' } },
      { t: 0.15, say: [0.15, 2.0] },
    ],
  },
  'tache echouee': {
    situation: 'l\'export a echoue',
    expect: 'il est preoccupe : aucun sourire ne traine, le regard reste sur vous',
    steps: [
      { t: 0.0, state: 'SPEAKING' },
      { t: 0.05, ask: { intent: 'amuse' } },
      { t: 0.15, say: [0.15, 1.2] },
      { t: 1.3, ask: { intent: 'report_failure' } },
      { t: 1.4, say: [1.4, 3.2] },
    ],
  },
  'l\'utilisateur explique longtemps': {
    situation: 'une longue demande, avec des hesitations',
    expect: 'il ecoute : de petits hochements aux pauses, jamais pendant qu\'on parle',
    steps: [
      { t: 0.0, state: 'LISTENING' },
      { t: 0.2, hear: [0.2, 9.0] },
    ],
  },
  'il ne se passe rien': {
    situation: 'une minute sans rien',
    expect: 'rien a signaler : il est la, il cligne, rien ne se repete',
    steps: [
      { t: 0.0, state: 'LISTENING' },
    ],
  },
};

/** La duree d'une situation : son dernier evenement, plus de quoi voir la fin. */
export function durationOf(s) {
  let end = 0;
  for (const step of s.steps) {
    end = Math.max(end, step.t);
    if (step.say) end = Math.max(end, step.say[1]);
    if (step.hear) end = Math.max(end, step.hear[1]);
  }
  return s === SITUATIONS['il ne se passe rien'] ? 60 : end + 2.5;
}

/**
 * La voix de JARVIS a l'instant `t` : des syllabes a ~4.5/s, une sur quatre
 * appuyee, une pause entre deux phrases de ~1.4 s. Deterministe.
 */
export function voiceAt(steps, t) {
  for (const step of steps) {
    if (!step.say) continue;
    const [a, b] = step.say;
    if (t < a || t >= b) continue;
    const local = t - a;
    const phrase = local % 1.8;
    if (phrase > 1.4) return 0;             // une pause de phrase
    const i = Math.floor(local * 4.5);
    const d = local - i / 4.5;
    const peak = i % 4 === 0 ? 0.8 : 0.42;
    return d < 0.06 ? peak * d / 0.06 : Math.max(0, peak * (1 - (d - 0.06) / 0.11));
  }
  return 0;
}

/** Le micro de l'utilisateur a l'instant `t` : une voix coupee de pauses. */
export function micAt(steps, t) {
  for (const step of steps) {
    if (!step.hear) continue;
    const [a, b] = step.hear;
    if (t < a || t >= b) continue;
    const local = t - a;
    // Une pause de 0.6 s toutes les 2.4 s : on hesite, on reprend.
    if (local % 2.4 > 1.8) return 0.02;
    return 0.02 + 0.32 * (0.4 + 0.6 * Math.max(0, Math.sin(local * 2 * Math.PI * 4.1)));
  }
  return 0.02;
}
