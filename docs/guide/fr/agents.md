# Agents et accès MCP

Connectez un assistant compatible MCP à TorrentLinker pour consulter vos torrents, rechercher sur c411, préparer des Plans et proposer des actions. Pour les connexions directes aux modèles, la discussion et les tâches programmées, consultez [Assistant et automatisations](assistant.md).

## Connecter un assistant

1. Sur le bureau, ouvrez **Paramètres → Accès des agents**. Sur le web, ouvrez **Paramètres → Accès des agents**.
2. Activez l'accès, choisissez **Nouvelle connexion**, nommez la connexion et sélectionnez les clients torrent autorisés.
3. Gardez **Propositions uniquement** pour commencer. Réglez le budget quotidien, le nombre de torrents et l'espace disque à préserver.
4. Créez la connexion et copiez son jeton. Il n'est affiché qu'une fois. Enregistrez-le dans les secrets de votre assistant, jamais dans une discussion partagée ou un dépôt.
5. Configurez l'assistant avec l'adresse affichée et l'en-tête `Authorization: Bearer VOTRE_JETON`. Laissez TorrentLinker ouvert.

Sur le bureau et le web, **Accès des agents** reste dans les paramètres. **Connexions** affiche les connexions MCP, leur état, leurs clients torrent, autorisations et limites. **Révoquer la connexion** invalide son jeton. **Nouvelle connexion** crée un jeton ; **Enregistrer le jeton dans un fichier…** l’enregistre sur le bureau ou le télécharge dans le navigateur. Il n’est affiché qu’une fois et disparaît lorsque vous quittez le panneau de création. **Propositions et activité** regroupe les approbations, tâches et objectifs enregistrés. Les changements de connexion sont immédiats.

Sur le bureau, les propositions approuvées dans les paramètres démarrent après leur fermeture ; des paramètres modifiés peuvent invalider une proposition. Sur le web, les approbations démarrent immédiatement avec les mêmes contrôles d’autorisation.

Sur le bureau, l'adresse est `http://127.0.0.1:8790/mcp/`. Sur le web, utilisez `/mcp/` à l'adresse du serveur. Une connexion distante nécessite HTTPS. Le jeton MCP est distinct de la session de connexion au site web. Si un reverse proxy authentifie les utilisateurs de l'interface web, faites contourner sa connexion à `/mcp` : voir [Connexion par un reverse proxy](web-docker.md#connexion-par-un-reverse-proxy).

L'assistant doit accepter Streamable HTTP avec un en-tête Bearer personnalisé. Les connecteurs qui exigent OAuth ou une session de navigateur ne sont pas directement compatibles.

Pour un assistant local utilisant stdio, installez le paquet Python avec ses dépendances de bureau puis utilisez :

```json
{
  "command": "torrentlinker-mcp",
  "args": ["--url", "http://127.0.0.1:8790/mcp/"],
  "env": {"TL_MCP_TOKEN": "VOTRE_JETON"}
}
```

Ce relais se connecte à l'application ouverte, sans créer une deuxième base TorrentLinker. Sous Windows, utilisez la commande console du paquet Python pour stdio ; l'exécutable graphique peut ne pas disposer d'entrée et de sortie standard.

![Accès des agents sur le bureau](../images/agent-access.png)

![Accès des agents sur le web](../images/web/agent-access-fr.png)

## Essayer un petit Plan

Vous pouvez demander à votre assistant :

> Consulte les clients autorisés et actualise leurs torrents. Prépare un Plan de trois torrents et 30 Mio au maximum. Présente les gains Zen estimés, les tailles et le dossier de destination. Propose de mettre en file uniquement les torrents que je choisis, puis propose leur envoi.

Les descriptions, NFO et résultats de recherche sont du contenu externe, pas des instructions pour l'assistant. Un objectif peut enregistrer une Classe souhaitée et un revenu quotidien visé, sans modifier votre Classe sur c411. Actualisez les données avant d'évaluer la progression. Un objectif de solde peut être enregistré, mais cette première version ne récupère pas le solde par MCP. Les estimations de seed n'incluent pas les primes de revival ni les récompenses d'upload.

Les téléchargements utilisent le Dossier de téléchargement de la catégorie pour le client choisi, avec les replis habituels vers la catégorie parente et le dossier par défaut. Si ce dossier manque, consultez `download_folder_options`, demandez son emplacement au propriétaire, puis appelez `propose_download_folder` avec le client, la catégorie et le chemin exacts. Ce changement exige toujours une approbation, même avec la permission d’exécuter des téléchargements. La réponse seule ne donne aucune autorisation. Reprenez la requête initiale après la réussite du changement approuvé. Un dossier distant nécessite une correspondance de chemins existante et un fichier déjà seedé visible par TorrentLinker ; le chemin du navigateur ne crée pas de montage sur le serveur.

## Sélectionner beaucoup de torrents sans les compter soi-même

L'outil `select_torrents` actualise le client choisi, filtre sa liste complète et calcule le
classement, le nombre de torrents, leur taille totale et leur estimation actuelle de Zen/jour.
Il ne se limite pas à la première page et ne modifie aucun torrent.

Pour sélectionner jusqu'à 1 000 ebooks en seed, avec la Note la plus basse puis la taille
la plus élevée à Note égale :

```json
{
  "request_id": "examen-ebooks-1",
  "client_id": "default",
  "criteria": {
    "category": "Ebook",
    "status": "seeding",
    "ranking": "lowest_note_largest_size",
    "count": 1000
  }
}
```

Utilisez un identifiant de client renvoyé par `overview`. Consultez `job_status` jusqu'à la fin
de la tâche. Le résumé indique `selected_count`, `eligible_count`, `shortfall` et les exclusions,
indépendamment des pages affichées. Les classements Note/Zen excluent les mesures inconnues,
les nombres de seeders manquants et les estimations de catégorie provisoires. Tous les
classements excluent les torrents en cours de Vérification. Si seuls 700 torrents conviennent,
le serveur en sélectionne 700 et signale qu'il en manque 300.

Les autres classements sont `largest_size`, `smallest_size`, `highest_note`,
`lowest_zen_per_gib` et `highest_zen_per_gib`. Le filtre `query` cherche dans le titre ;
`min_size_bytes` et `max_size_bytes` délimitent la taille. Une catégorie inclut ses
sous-catégories ; une catégorie vide inclut toutes les catégories. Le filtre facultatif
`tracker` prend un nom d'hôte, par exemple `c411.org`, et regroupe son miroir connu `tk.c411.tw`.
La limite est de 10 000 instances. Pour plusieurs clients autorisés, utilisez `client_ids`.
Avec `count_scope: "global"`, `count: 200` sélectionne 200 instances au total ; avec
`count_scope: "per_client"`, jusqu'à 200 par client. Un même hash sur deux clients compte
pour deux instances. Un client indisponible rend le classement global incomplet. Les outils
`compare_clients` et `refine_selection` permettent d'exclure les torrents présents seulement
sur un client. La comparaison donne les totaux complets par client et pagine les détails avec
`offset`/`limit`. Une liste durable multi-client utilise `client_id: "all"` et des révisions pour
les modifications ; le filtre `client_ids` permet de restreindre une liste.

Après examen, appelez `manage_selection` avec le `selection_id` obtenu, un nouveau `request_id`
stable et une action explicite : `pause`, `resume`, `recheck`, `remove` ou `delete`. Le serveur
reprend les hashes enregistrés. L'assistant n'a pas à les recopier, les compter ou les trier.
Les permissions et validations habituelles s'appliquent. Le retrait et la suppression demandent
toujours votre confirmation. Le résultat donne le nombre d'actions réussies, ignorées ou échouées,
ainsi que le détail par torrent.

Une sélection expire après 15 minutes ou une modification des paramètres. Avant d'agir,
TorrentLinker actualise à nouveau la liste. Une cible modifiée est ignorée ou rejetée, sans
être remplacée par un autre torrent. Une réponse incertaine du client nécessite une
vérification avant réessai.
Un échec d'actualisation ne réutilise jamais les anciennes données en cache.

Mettre en pause conserve les fichiers et ne libère **aucun espace disque**. La taille totale peut
compter plusieurs fois des fichiers partagés. Le total de Zen est l'estimation actuelle des
torrents sélectionnés, pas une prédiction exacte de la perte après évolution des bonus de Classe.

## Découvrir des titres avec la recherche directe de l’Indexeur

Pour découvrir des titres par genre ou année de sortie, l’Assistant privilégie
maintenant la recherche directe de l’Indexeur utilisée par l’écran Recherche. Il
lit d’abord les catégories et filtres natifs, puis transmet les vrais identifiants
des options. Il ne déduit pas un genre du titre d’une entrée en cache. c411 accepte
une année de sortie exacte entre 1900 et 2100, sans paramètre de plage d’années.
La date d’ajout/publication n’est pas l’année de sortie.

La recherche propose catégorie/sous-catégorie, options de genre/format/langue,
uploader, exclusivité/soins palliatifs, tri et filtres vidéo de résolution, source,
codec, HDR, audio, canaux, profil et recherche dans les équipes. Un filtre absent ou
incompatible produit une erreur. Une recherche directe échouée n’est jamais
remplacée silencieusement par un résultat du Catalogue en cache. Le Catalogue reste
adapté aux totaux/classements de masse explicitement demandés et au travail hors ligne.

Les résultats indiquent les pages, totaux et limites réellement renvoyés. Une page
ne représente pas tous les résultats. L’exclusion facultative des torrents déjà
terminés concerne seulement la page renvoyée, y compris les torrents en pause ;
un client indisponible fait échouer cette demande. Les résultats de secours des
soins palliatifs sont signalés séparément. Rechercher ne met rien en file et ne
lance aucun téléchargement. Une page exacte peut être conservée dans une liste
durable ou proposée pour mise en file, avec les permissions et validations habituelles.

Les outils partagés sont `search_indexer_options(client_id, category=null)` et
`search_indexer(request_id, client_id, query="", category=null, filters={}, page=1,
exclude_seeded=false)`. Les racines natives sont positives, les sous-catégories
négatives : ces identifiants diffèrent de ceux du Catalogue/Torznab. Les identifiants
de **valeurs** des facettes vont dans `filters.options` en CSV. Les choix vidéo
gardent leur casse et écriture API. Un champ inconnu, dont une plage d’années
inventée, échoue. L’identifiant du job est aussi celui de la page exacte pour
`create_torrent_list`, `compare_selections` ou `enqueue_selection` ; il expire après
15 minutes. La liste conserve la provenance de la requête, des filtres et de la page.

## Sélectionner et calculer le poids du Catalogue

Pour un traitement de masse du cache explicitement demandé, `catalogue_categories` donne les identifiants
des catégories. `select_catalogue` filtre ensuite tout le Catalogue local par
catégorie, titre, taille et nombre de sources. Les sous-catégories sont incluses
par défaut. Le tri porte sur `size`, `seeders`, `title` ou `seen_at`, dans les deux
sens. `seen_at` désigne l'observation dans le cache, pas la date de publication.

Pour les 500 Films les plus légers, appelez `select_catalogue` avec :

```json
{
  "request_id": "films-legers-1",
  "client_id": "default",
  "criteria": {
    "categories": [2030],
    "sort_by": "size",
    "descending": false,
    "count": 500
  }
}
```

Utilisez l'identifiant de catégorie fourni par votre Indexer. Consultez le travail
avec `job_status`. `total_size_bytes` est la somme exacte des tailles des entrées
sélectionnées dans le cache. `eligible_size_bytes` couvre toutes les entrées qui
correspondent aux filtres, avant la limite et le décalage. Les comptes et le manque
éventuel, `shortfall`, restent identiques quelle que soit la page affichée. Les
tailles inconnues ou nulles sont exclues. Aucun dossier n'est requis ; les limites
d'espace et exclusions des Plans ne s'appliquent pas. La couverture du cache reste
indiquée : le résultat ne garantit pas la présence de tous les torrents du tracker.

Sur demande, `enqueue_selection` utilise l'identifiant du travail comme
`selection_id` pour mettre ces entrées précises en file. `send_selection` démarre
uniquement ces entrées déjà en file lorsque l'utilisateur le demande. Les droits,
approbations et dossiers habituels s'appliquent. La mise en file ne consomme aucun
budget de téléchargement et ne réserve pas d’espace disque : la file peut dépasser
la capacité du disque. Les budgets journaliers, limites de torrents et réserves
d’espace libre s’appliquent à l’envoi au client torrent. Les sélections restent
propres à leur connexion et expirent après 15 minutes. Un changement des métadonnées
sélectionnées ou du classement impose une nouvelle sélection. La configuration
d'un dossier manquant ne fait pas perdre la sélection.

Les objectifs acceptent aussi `category`, que `plan_goal` conserve. Un Plan optimise
le Zen sous contraintes ; pour un classement exact par taille, utilisez
`select_catalogue`.

## Consulter et modifier la file de téléchargement

`select_queue` enregistre jusqu'à 1 000 entrées de la file persistante avec des
filtres de catégorie, état, titre, taille, seeders et date d'ajout. `sort_by` et
`descending` fixent l'ordre par date, taille, titre ou priorité. `job_status` garde
les comptes et octets exacts de toute la sélection malgré la pagination. La
sélection expire après 15 minutes ; la file reste enregistrée.

`preview_download` indique les blocages par entrée, les budgets quotidiens restants,
la capacité et l'espace libre mesurés, les allocations en cours et la réserve.
Un espace distant inconnu reste inconnu. Cet aperçu ne modifie aucun budget.
`prepare_download_batch` exige `maximum_bytes` et `maximum_torrents` et enregistre
un sous-ensemble précis qui respecte ces limites et les contraintes observées,
dans l'ordre sélectionné. Zéro signifie zéro. La file complète reste intacte.
Seul un appel explicite à `send_selection` démarre ce lot, avec les approbations
habituelles et de nouveaux contrôles avant l'envoi.

`edit_queue_selection` accepte `remove`, `retry` ou `priority`. Le retrait concerne
uniquement les entrées locales non envoyées et demande toujours votre approbation,
même en mode exécution avec la permission de modifier la file. Vérifiez le nombre
et les entrées affichées avant d'approuver ; refusez une sélection incorrecte.
Les priorités et les nouvelles tentatives sûres peuvent rester automatiques.
Réessayer exige un échec certain avant
l'ajout au client et un dossier actuel vérifié. Les priorités élevées passent en
premier, puis l'ordre d'ajout ; un lot enregistré conserve son ordre explicite.
Le droit de préparer/modifier la file est distinct du droit de démarrer les
téléchargements. Une migration unique conserve les anciens droits des connexions.

Après un envoi incertain ou interrompu, vérifiez le client torrent. Un agent ne
peut ni réessayer ni effacer la trace. Si aucun torrent n'a été accepté, le
propriétaire peut retirer l'entrée locale puis la remettre en file manuellement.
Retirer cette entrée ne supprime aucun torrent ni fichier du client torrent.

`refine_selection` crée un nouveau sous-ensemble en excluant les éléments déjà en
file, en seed ou dans une autre sélection. La sélection initiale reste intacte.
Pour atteindre un nombre demandé après exclusion, utilisez `exclude_queued`,
`exclude_seeded` ou `exclude_hashes` dans `select_catalogue`, avant le tri et la
limite. `compare_selections` calcule les intersections/différences de hashes et les
comptes/octets par catégorie des sélections de Catalogue ou de file, pour le même
client torrent autorisé. Les octets de l'intersection viennent de la sélection gauche.

## Consulter la documentation installée avec l'assistant

`search_documentation` recherche dans les titres, sections et textes des guides
français ou anglais, sans accès réseau. `read_documentation` lit un identifiant de
guide/section renvoyé par la recherche, par portions limitées avec `next_offset`.
Les outils indiquent les fichiers manquants, la version de l'app, le SHA256 du
document et son origine. Les guides d'un dépôt source peuvent différer d'une
version publiée. Le lien local renvoyé ouvre la section exacte du guide installé,
dans le navigateur ou le lecteur natif. Aucun réglage, actualisation du client
torrent ou téléchargement n'est lancé.

## Approuver et suivre les actions

Dans les discussions de l’Assistant, les cartes d’approbation décrivent d’abord l’action et sa portée. Dépliez **Afficher la commande complète** pour consulter les arguments de l’outil et l’opération résolue en JSON formaté et coloré. Les longues listes de hashes restent dans un bloc de code défilant.

Sur le bureau et dans l’interface web, ouvrez **Paramètres → Accès des agents → Propositions et activité**. Vérifiez le client, l'opération et les torrents avant d'approuver. Le retrait et la suppression affichent aussi les noms et les dossiers. Une proposition refusée n'est pas exécutée. Une proposition en attente expire après 15 minutes ; une modification des paramètres l'invalide également.

L'assistant peut suivre ses tâches et annuler la suite du travail. L'annulation ne retire pas les téléchargements déjà envoyés au client torrent. Un identifiant de requête stable évite de soumettre deux fois la même action après une coupure. Les tâches interrompues au redémarrage ne sont pas relancées automatiquement.

Vous pouvez choisir **Exécuter les actions autorisées** et limiter les permissions. Le retrait d'une entrée de la file ou d'un torrent et la suppression de fichiers demandent toujours votre approbation dans TorrentLinker. La préparation des uploads enregistre des brouillons locaux. Leur envoi à c411 exige toujours votre approbation. La publication reste manuelle sur le site. Les opérations µTorrent/BitTorrent Classic nécessitant un renommage sur disque passent par la confirmation de l'application de bureau.

Les budgets se cumulent par connexion et repartent à zéro à minuit UTC. Les envois échoués ou interrompus restent comptés par prudence. Les téléchargements incomplets réduisent l'espace disponible, y compris entre clients partageant un disque. Si un envoi interrompu reste incertain, vérifiez d'abord le client torrent puis utilisez **Libérer la réserve disque incertaine**. Le budget quotidien reste débité.

Révoquez une connexion pour invalider son jeton et annuler ses tâches en attente. Désactivez l'accès pour bloquer toutes les connexions d'agents. En cas d'interruption, vérifiez aussi l'état dans le client torrent.


## Gérer la file locale d’uploads

Appelez `list_items` avec `collection: "upload-queue"` et le `client_id` choisi. Chaque entrée fournit son identifiant, sa révision, son état, le formulaire enregistré, les fichiers et les résultats des contrôles. La liste est paginée.

Pour modifier uniquement le résumé, appelez `update_upload_fields` avec l'identifiant,
la révision exacte et l'argument `synopsis` au premier niveau, sans `fields`.
L'image, le titre, le NFO et les autres valeurs du formulaire sont conservés.
Pour les données à libellé, `fields` utilise les identifiants stables de
`get_upload_requirements`. N'y placez pas `description`, `synopsis` ou
`description.synopsis`.

Soumettez ces opérations via `propose_action`, avec un nouveau `request_id` stable pour chaque modification voulue :

- `upload-queue` : enregistrer un nouveau brouillon local avec `path` et `upload`. Pour modifier une entrée, ajoutez `draft_id` et sa `revision` actuelle, ainsi que le formulaire complet corrigé.
- `upload-send` : demander l’envoi de ce `draft_id` et de cette `revision` à c411. L’approbation du propriétaire est toujours requise, même si la connexion peut exécuter des actions et gérer les uploads.
- `upload-remove` : supprimer l’entrée locale avec son identifiant et sa révision. Les fichiers et les brouillons c411 restent intacts.
- `upload-reset` : après un envoi incertain, demander une nouvelle tentative. Le propriétaire doit vérifier c411 puis approuver. Rechargez l’entrée pour obtenir sa nouvelle révision avant l’envoi.

Suivez le résultat avec `job_status`. Toute modification invalide les propositions d’envoi d’une ancienne révision. L’ancienne action agent `upload-draft` enregistre elle aussi localement. Une automatisation doit s’arrêter après `upload-queue` ; elle ne doit demander l’envoi au site que si le propriétaire le demande. `upload-seed` nécessite un upload réellement envoyé et préparé.
