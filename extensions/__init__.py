"""
extensions/ — des capacités qu'on n'écrit pas : serveurs MCP et Agent Skills.

Paquet racine au sens du contrat V2 : rien ici n'importe main.py. Les plugins
`plugins/mcp_bridge.py` et `plugins/skills.py` l'exposent à Gemini ; les
supprimer rend JARVIS identique à avant.

  mcp.py     client MCP (stdio et HTTP « streamable ») : chaque serveur
             configuré dans config/mcp.json devient utilisable par UN seul outil
             `mcp`, quel que soit le nombre d'outils qu'il apporte.
  skills.py  dossiers SKILL.md (format ouvert d'Anthropic et d'OpenAI) : seuls
             les noms et descriptions vont à Gemini, les instructions sont lues
             quand une tâche en a besoin.

Idées reprises de bertrandmbanwi/Jarvis (MIT, Copyright (c) 2026
bertrandmbanwi) : format de config de Claude Desktop, résultats étiquetés
comme données d'un tiers, confirmation sauf `auto_approve`, divulgation
progressive des skills, lecture confinée au dossier du skill. Le code est
réécrit : synchrone (les plugins tournent dans un fil) et sans le SDK `mcp`.
"""
