import pandas as pd
auth = pd.read_csv('author_2023_trades.csv')
df = pd.read_csv('longterm_majors_2008_2024.csv')
bot = df[(df['symbol'] == 'EURUSD') & (pd.to_datetime(df['date']).dt.year == 2023)].copy()

auth_set = set(auth['date'].unique())
bot_set = set(bot['date'].unique())

print(f"Author trade dates: {len(auth_set)}")
print(f"Bot trade dates:    {len(bot_set)}")
print(f"Common dates:       {len(auth_set & bot_set)}")
print(f"Author ONLY dates:  {len(auth_set - bot_set)}")
print(f"Bot ONLY dates:     {len(bot_set - auth_set)}")

common = sorted(list(auth_set & bot_set))
print("\n--- Matching Days ---")
for d in common[:10]:
    a = auth[auth['date'] == d].iloc[0]
    b = bot[bot['date'] == d].iloc[0]
    print(f"{d} -> Author: {a['bias']} {a['result']} ({a['rr']}R) vs Bot: {b['dir']} {b['result']} ({b['rr']}R)")

print("\n--- Bot ONLY Days (Days author did NOT trade) ---")
bot_extra = bot[bot['date'].isin(bot_set - auth_set)]
print(f"Bot extra trades count: {len(bot_extra)}")
print(f"Bot extra wins: {(bot_extra['result']=='TP').sum()}, losses: {(bot_extra['result']=='SL').sum()}")
print(f"Bot extra PnL: {bot_extra['rr'].sum():.2f} R")

print("\n--- Author ONLY Days (Days bot missed) ---")
auth_extra = auth[auth['date'].isin(auth_set - bot_set)]
print(f"Author extra trades count: {len(auth_extra)}")
print(f"Author extra wins: {(auth_extra['result']=='TP').sum()}, losses: {(auth_extra['result']=='SL').sum()}")
print(f"Author extra PnL: {auth_extra['rr'].sum():.2f} R")
