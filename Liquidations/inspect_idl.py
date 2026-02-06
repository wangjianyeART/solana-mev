import json
import sys

file_path = "/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_lending.json"

try:
    with open(file_path, 'r') as f:
        data = json.load(f)
        
    instructions = data.get('instructions', [])
    targets = ['flash', 'liquidate', 'refresh']
    
    found = []
    for instr in instructions:
        name = instr.get('name', '').lower()
        if any(t in name for t in targets):
            found.append(instr)
            
    print(json.dumps(found, indent=2))
    
except Exception as e:
    print(f"Error: {e}")
