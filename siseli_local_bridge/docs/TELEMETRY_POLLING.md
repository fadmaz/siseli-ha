# Forcer la remontée de télémétrie (dev_rpc) — journal d'investigation

> **Dénouement (2026-09-12) : PARTIELLEMENT RÉSOLU.** Le correctif de la pile TCP
> (retransmission, validation RST, remplacement de connexion) a bien réglé un vrai bug de
> notre côté : un poll perdu désynchronisait le flux d'envoi *définitivement*. Vérifié en
> production le 12/09 (21+ cycles consécutifs à 20s) ET le 13/09 (9 cycles à 15s) — mais
> **le 13/09, la même session s'est ensuite arrêtée de répondre après ~15s de polling
> réussi**, sans aucune perte de paquet cette fois (ACK TCP propre confirmé par capture
> live, `dev_rpc` toujours envoyé toutes les 15s) : le dongle accuse réception au niveau
> TCP mais ne génère plus de `dev_rpc_reply` au niveau applicatif. **Cause différente,
> toujours pas identifiée** — le correctif du 12/09 a éliminé UNE cause de silence
> (désynchronisation par perte de paquet), pas toutes. Voir « Résolution » puis
> « Rechute du 13/09 » plus bas. Les verdicts intermédiaires ci-dessous restent
> pertinents comme historique du raisonnement, mais aucun n'est la conclusion finale.

Contexte : le dongle RWB1 ne remonte ses données que lentement et sur son propre
calendrier. Cette page documente les tentatives faites pour forcer une remontée plus
rapide via `TELEMETRY_POLL_INTERVAL_SEC`, ce qui a été corrigé au passage, et pourquoi
la fonctionnalité reste désactivée par défaut aujourd'hui.

## Le mécanisme : `dev_rpc`

Le vrai cloud Siseli envoie périodiquement (~26s dans une capture réelle) une requête
`dev_rpc` au dongle sur le topic `dtu/<id>/sub/service/dev_rpc` :

```json
{"c":5,"t":"<token>","s":"<token>","i":501,"b":{}}
```

Le dongle répond en général en moins de 300ms sur `pub/service/dev_rpc_reply` avec
l'intégralité de ses blocs de télémétrie. `siseli_local_bridge` (notre fausse cloud
locale) reproduit cette requête pour forcer une lecture à la demande, au lieu
d'attendre le push spontané du dongle.

`TELEMETRY_POLL_INTERVAL_SEC` (option de l'add-on, `0` = désactivé) contrôle
l'intervalle. Code : `fakecloud.py::poll_due_connections` /
`_build_dev_rpc_request`, thread dédié `core.py::telemetry_poll_loop`.

## Bugs corrigés (améliorations réelles, indépendantes du résultat final)

Ces corrections sont utiles et restent en place, que le polling soit activé ou non.

1. **Cadence bloquée à 10s au lieu de la valeur configurée.** Le contrôle du polling
   était appelé depuis `health_logger()`, qui ne tique que toutes les 10s — un
   `TELEMETRY_POLL_INTERVAL_SEC=5` se comportait donc comme 10s. Fix : thread dédié
   `telemetry_poll_loop()` à granularité 1s, indépendant de `health_logger`.
2. **`"i"` codé en dur à `501` sur chaque requête.** Le dongle semble dédupliquer par
   `i` et ignore silencieusement (ACK TCP mais aucune réponse applicative) tout
   message avec un `i` déjà vu. Fix : `conn.next_rpc_id`, incrémenté de 2 à chaque
   poll.
3. **Byte `\x00` de tête manquant.** Ce firmware préfixe TOUT PUBLISH (entrant comme
   sortant) d'un octet nul avant le JSON — déjà connu pour les réponses `_reply`, mais
   `_build_dev_rpc_request` avait été oublié. Sans ce byte, la requête est purement et
   simplement ignorée par le dongle (aucune erreur, aucun log, juste un ACK TCP nu).
4. **Contournement DNS par IP câblée en dur.** Le dongle saute parfois la résolution
   DNS de `dtu.access.solar.siseli.com` et se connecte directement à une IP cache
   (`8.212.16.60`). Ajout de `HTTP_STUB_REAL_IPS` pour intercepter aussi ce chemin
   (`core.py` route vers `httpstub.py` avec `local_ip=dst_ip`).
5. **RST silencieux.** `tcpstack.py` ignorait un RST du dongle sans rien logger,
   rendant impossible de distinguer "session coupée proprement" de "le dongle a juste
   arrêté de répondre". Un log dédié (`[LOCAL CLOUD] ... sent RST`) a été ajouté.

Avec ces quatre premiers correctifs, `dev_rpc` fonctionne réellement : une requête
correctement formée obtient une vraie réponse en moins de 300ms, à la cadence
configurée exacte (confirmé à 5.03s, 8.03s et 20s selon la configuration testée).

## Le vrai problème : ça marche, mais ça ne sert à rien au-delà des 1-2 premiers cycles

Une fois le mécanisme fonctionnel, trois expériences ont été menées :

### Test 1 — polling à 8s

Fonctionne au début, puis **la connexion MQTT meurt silencieusement** (aucun FIN/RST
vu) au bout de 1 à 10 minutes, obligeant une reconnexion complète (DNS → HTTP → MQTT)
qui peut prendre de quelques secondes à plusieurs minutes. Net négatif : plus de trous
qu'avec le rythme naturel du dongle.

### Test 2 — polling désactivé (référence)

Connexion **stable 27+ minutes sans une seule reconnexion**, avec des remontées
spontanées à un **rythme exact de 300 secondes (5 min pile)**, confirmé sur 5 cycles
consécutifs. Ce test a établi que le rythme natif du dongle est un fait fixe, pas une
lenteur aléatoire.

### Test 3 — reconnexion forcée après chaque lecture (hypothèse : ça réinitialiserait le minuteur)

- Cycle 1 (juste après un vrai power-cycle physique) : reconnecté en 3s, 1ère donnée
  en 51s. Semblait prometteur.
- Cycle 2 (dongle déjà "chaud") : 1ère donnée en 355s (5min56) — **plus lent** que le
  cycle naturel non perturbé.

Conclusion : le délai de 51s du cycle 1 était un artefact du redémarrage physique
(première annonce rapide après un boot), pas un effet de la reconnexion MQTT. Le
minuteur de report (~5 min) tourne indépendamment de la session MQTT et n'est **pas**
remis à zéro par une reconnexion logicielle.

### Test 4 — polling à 20s (compromis prudent)

Stable sur 1h40+ sans aucune reconnexion (contrairement au 8s). Mais `dev_rpc` ne
reçoit de vraies réponses que pendant les 1-2 premiers échanges après la connexion ;
ensuite le dongle arrête de répondre aux polls (silencieusement, ACK TCP seul) pour le
reste de la session, qui continue sur son rythme naturel de 5 minutes.

**Verdict sur le polling** : quel que soit l'intervalle testé (8s, 20s), le dongle
n'accepte qu'un nombre limité de `dev_rpc` non sollicités par session avant de les
ignorer. Forcer plus n'aide pas ; forcer trop vite (8s) déstabilise carrément la
connexion.

## Confirmation externe (recherche, 2026-09-12)

- Une intégration Home Assistant indépendante ([Conexo-Casa/solar-of-things-ha](https://github.com/Conexo-Casa/solar-of-things-ha)),
  qui interroge le **vrai cloud** Siseli via son API officielle, documente elle aussi
  un rafraîchissement fixe de 5 minutes, non configurable — confirme que ce n'est pas
  une limite de notre bridge mais du système cloud/firmware Siseli dans son ensemble.
- [smartess-poller](https://github.com/bohdan1krokhmaliuk/smartess-poller), un projet
  similaire pour la famille de dongles Eybond/SmartESS (même écosystème de dongles
  WiFi bon marché pour onduleurs Voltronic/PI30), confirme qu'interroger activement ce
  type de dongle **fonctionne** mais seulement entre 5 et 10 secondes d'intervalle :
  *"le micro du dongle et le bus série de l'onduleur sont lents ; trop solliciter
  cause des réponses perdues et des resets TCP"* — exactement le symptôme observé ici,
  même si dans notre cas 20s (pourtant plus prudent que leur recommandation) ne
  suffit pas non plus à obtenir des réponses continues.
- Les blocs déjà décodés par ce bridge (`eo8w`, `WdRR`, `2l0E`...) ressemblent
  structurellement au protocole ASCII Voltronic/PI30 (`QPIGS`, `QPIRI`...) une fois
  décodés en base64 — une piste non explorée pour un vrai temps réel serait de taper
  directement le port RS232 "WIFI" de l'onduleur (même connecteur que la RWB1) avec un
  adaptateur RS232 réel, en bypassant totalement le dongle/cloud. Précédent documenté :
  [SYG-MPPT-120A_grafana](https://github.com/PurpleAlien/SYG-MPPT-120A_grafana). Non
  tenté ici : demande du câblage physique et une nouvelle phase de rétro-ingénierie.

## Configuration retenue

*(Mise à jour 2026-09-12 — voir « Résolution » plus bas.)* `TELEMETRY_POLL_INTERVAL_SEC`
à **15s** en production (vérifié en direct le 2026-09-12 18:24-18:27 : 9 cycles
`dev_rpc_reply` consécutifs, exactement 15s d'écart, aucun raté) : depuis les
correctifs TCP déployés en **2.6.30**, chaque poll obtient une réponse complète (plus
seulement les 1-2 premiers de la session), soit une télémétrie fraîche toutes les 15 s
au lieu du rythme natif de 5 minutes. L'ancienne recommandation « ne pas descendre sous
~10-15s » reposait sur l'instabilité à 8s, vraisemblablement causée par le défaut de
retransmission corrigé depuis — un retest à 10s ou moins est raisonnable, mais n'a pas
encore été fait.

## Effet de bord non résolu : bootstrap parfois lent — RÉSOLU, voir « Résolution »

Indépendamment du polling, le cycle de reconnexion complet (DNS → HTTP → MQTT) met
parfois plusieurs minutes et plusieurs tentatives avant d'aboutir, chaque tentative
ratée se terminant par un RST du dongle après un délai fixe d'environ 75 secondes.
Hypothèse posée (non confirmée en direct faute d'avoir reproduit le cas avec le
diagnostic actif) : un second SYN à un ISN différent pour une connexion encore
ouverte remplace silencieusement l'état de celle-ci dans `tcpstack.py`
(`CONNECTIONS[key] = conn` écrase l'existante sans vérifier si elle a déjà commencé
à être utilisée), désynchronisant le suivi de séquence et faisant que la vraie
requête du dongle est prise pour un doublon et jamais traitée jusqu'à l'abandon côté
dongle. Du log de diagnostic a été ajouté (`[LOCAL CLOUD DIAG]`) dans
`tcpstack.py::handle_tcp` et `Connection.receive` pour confirmer ça lors d'un futur
bootstrap raté, mais aucun cas n'a encore été capturé en action — investigation
laissée en pause.

## Résolution (2026-09-12) : la cause était notre pile TCP, pas le dongle

Trois défauts dans `tcpstack.py` (et un faux diagnostic dans `fakecloud.py`), corrigés
en 2.6.30, expliquaient à eux seuls **tous** les symptômes ci-dessus :

1. **Pas de retransmission de nos propres segments.** Le choix initial (« un saut LAN,
   payloads minuscules, une perte est rare ») ignorait que le dongle est en WiFi. Un
   seul poll `dev_rpc` perdu désynchronisait notre flux d'envoi *définitivement* :
   chaque poll suivant se trouvait « au-delà du trou », la pile TCP du dongle ne le
   remettait jamais à son application et se contentait de ré-ACKer l'ancienne position
   — exactement le « ACK TCP nu, plus jamais de réponse » des tests 1 et 4. Corrigé :
   suivi des ACK entrants (`note_ack`) + file de retransmission avec backoff
   (`retransmit_due`, tick 1 s dans `core.py::telemetry_poll_loop`, désormais démarré
   dès que `LOCAL_CLOUD_IP` est défini) + verrou par connexion (le thread de poll et
   le sniffer émettaient sur la même connexion sans synchronisation de `our_seq`).
2. **Remplacement aveugle d'une connexion établie sur un SYN à ISN différent** —
   l'hypothèse du paragraphe précédent, confirmée par le code. Corrigé façon
   RFC 793/5961 : challenge-ACK en gardant l'état ; un dongle qui a vraiment
   redémarré sa socket répond par un RST valide qui libère proprement l'entrée.
3. **RST non validé.** Un RST périmé (réponse du dongle à un vieux SYN-ACK d'une
   connexion abandonnée) tuait la connexion *courante* pour la même clé — d'autant
   plus probable que ce firmware réutilise des ISN quasi séquentiels et des ports
   sources bas d'une tentative à l'autre. Corrigé : le RST n'est honoré que si son
   `seq` égale `their_next_seq` (style RFC 5961), sinon loggé `stale RST ignored`.
4. **L'hypothèse « dédup par `i` » (bug n°2 de la liste historique) était fausse.**
   La capture `dongle_reboot.pcap` montre le vrai cloud envoyant `"i":501` sur
   *chaque* requête (t=0 s et t=25,4 s, même valeur), répondue à chaque fois.
   `_build_dev_rpc_request` est revenu à un `i` constant de 501, identique au vrai
   cloud octet pour octet. Les réponses silencieusement « ignorées » étaient en
   réalité le symptôme du défaut n°1.

**Vérification en production** (TELEMETRY_POLL_INTERVAL_SEC=20, session du
2026-09-12 17:25→17:32+) : `dev_rpc_reply` décodé et publié toutes les 20 s pile,
21+ cycles consécutifs sans un seul raté, y compris à travers plusieurs
retransmissions de fragments par le dongle. Le bootstrap complet DNS → HTTP → MQTT
a abouti en 2 tentatives (~90 s). À noter, observé au passage dans la capture : le
dongle attend parfois ~26 s après le handshake HTTP avant d'envoyer son POST — une
lenteur firmware à ne pas confondre avec un blocage.

Descendre à 10 s est vraisemblablement possible (l'instabilité historique à 8 s
collait au profil du défaut n°1) mais reste à retester.

## Limite résiduelle : le cache interne du dongle (~60 s), sondes tentées (2026-09-12)

Avec le polling fiabilisé, la fraîcheur réelle est bornée par le dongle lui-même :
`dev_rpc` lit un **cache** que le firmware rafraîchit par familles de blocs décalées,
chacune sur un cycle d'environ 60 s (mesuré par hash de chaque bloc sur 2 cycles :
`WdRR`+`2l0E` à t=30 s et t=90 s ; `2ONL`+`Yavb`+`COST` à t=37 s et t=105 s). Le `ts`
des réponses avance à chaque poll (horodatage de réponse), mais les mesures ne bougent
que lorsque l'ordonnanceur série interne repasse sur la famille concernée. Poller plus
vite que ~15 s ne réduit donc que la latence de récupération d'un instantané, pas son âge.

Sondes lecture seule tentées pour forcer une lecture série à la demande — **toutes
ignorées silencieusement** (ACK TCP nu, session intacte, polls suivants normaux) :

| Sonde | Résultat |
|---|---|
| `dev_rpc` avec `"b":{"cn":"2ONL"}` (sélection de bloc) | ignorée |
| topic `sub/service/dev_prop_get` | ignorée |
| topic `sub/service/dev_ctrl` | ignorée |
| topic `sub/service/dev_read` | ignorée |
| `dev_rpc` avec `"c":6` | ignorée |

Le firmware ne répond qu'à la forme exacte `{"c":5,...,"b":{}}` sur `dev_rpc` parmi tout
ce qui a été essayé, et il est robuste aux messages inconnus. Outil : hook expérimental
`fakecloud.py::_send_probe_if_present` (déposer `/tmp/probe.json` dans le conteneur —
`{"service":..., "payload":...}` — envoyé une fois au tick suivant, jamais actif sans ce
fichier). Pistes restantes pour du plus frais que 60 s : capturer l'app officielle
pilotant le dongle via le vrai cloud (écriture de réglage = forcément un canal
commande non encore observé), analyse du firmware RWB1, ou le RS232 direct déjà noté.

## Rechute du 2026-09-13 : le silence applicatif revient même avec la forme standard

Contexte : entre-temps, le bridge est repassé plusieurs fois entre mode local et mode
passif (voir `protocole-cloud-dongle/README.md` section 6, investigation du canal de
commande), avec plusieurs redémarrages du conteneur. Après le retour en mode local
(`TELEMETRY_POLL_INTERVAL_SEC=15`), l'utilisateur a observé une remontée toutes les
~60s pendant un moment, puis un retour à un rythme de 5+ minutes.

**Vérifié par capture live** : `dev_rpc` avec `"b":{}}` (la forme standard, exactement
celle documentée ci-dessus comme "la seule qui marche") continue d'être envoyé toutes
les 15s pile (`i` incrémenté normalement : 569, 571...), et le dongle **accuse
réception au niveau TCP** (ACK propre, aucune perte, aucun retransmit déclenché) mais
**ne publie plus de `dev_rpc_reply`** après le premier succès de la session (une seule
réponse obtenue, à 15s après le CONNECT, puis silence applicatif pur jusqu'au push
naturel suivant du dongle).

**Ce n'est donc pas le bug de la pile TCP corrigé le 12/09** (pas de désynchronisation
de séquence : l'ACK reçu est cohérent avec ce qui a été envoyé) **ni la limite de
cache de 60s documentée ci-dessus** (qui prédit une réponse à chaque poll, juste avec
des valeurs pas plus fraîches que le cycle interne — pas un silence total). C'est un
troisième phénomène, distinct des deux précédents, non encore expliqué : le firmware
du dongle cesse de répondre à `dev_rpc` après un certain temps/nombre de requêtes dans
une session, sans RST ni FIN, sans erreur visible d'aucun côté. Le service continue de
fonctionner normalement par ailleurs (le push spontané `dev_prop_post` continue sur son
rythme propre).

**Non résolu.** Pistes non explorées : est-ce lié au nombre de requêtes envoyées
(fatigue/quota interne après N polls), à un délai fixe depuis le CONNECT, ou aux
redémarrages répétés du bridge aujourd'hui (état résiduel côté dongle) ? Diagnostic
ajouté dans `fakecloud.py` (voir plus bas) pour capturer le moment exact où ça
bascule lors d'une prochaine session de test.

**Note (2026-09-13, après déploiement du diagnostic) : un simple Rebuild+Restart a
suffi à débloquer la situation** — après redémarrage, `dev_rpc_reply` est reparti en
continu toutes les 15s sans interruption sur plusieurs minutes, sans qu'aucun "poll
stall" ne soit loggé. Ça confirme qu'un restart est un remède pratique immédiat si le
blocage revient, même si la cause de fond reste inconnue.

## Diagnostic ajouté (2026-09-13) : compteur polls envoyés / réponses reçues

Pour caractériser la rechute ci-dessus sans deviner, `fakecloud.py` trace maintenant,
par connexion : le nombre de `dev_rpc` envoyés (`poll_sent_count`), le nombre de
`dev_rpc_reply` effectivement reçus (`poll_reply_count`), et l'instant du dernier
succès. Dès qu'un poll est envoyé sans qu'aucune réponse ne soit arrivée avant le
suivant, un log `[LOCAL CLOUD DIAG] poll stall` est émis une seule fois (pas à chaque
tick) avec : le nombre de polls envoyés depuis le dernier succès, le temps écoulé
depuis le dernier succès, et le temps écoulé depuis le CONNECT. De quoi répondre
directement à "après combien de requêtes ?" et "après combien de temps ?" la prochaine
fois que ça se reproduit, sans avoir à recouper des captures tcpdump à la main.
