"""Compare the Author's Real Notion Trades with the Mechanical Bot's Trades on the SAME asset and SAME days.
"""
import sys, os
import pandas as pd
import numpy as np

if sys.platform == 'win32':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

author = pd.read_csv('silver_bullet_notion_clean.csv')
bot = pd.read_csv('method5_silver_bullet_clean.csv')

# Let's look at EUR/USD in 2023
auth_eur23 = author[(author['Pair'] == 'EUR/USD') & (author['Year'] == 2023)].dropna(subset=['Result']).copy()
bot_eur23 = bot[(bot['symbol'] == 'EURUSD') & (bot['year'] == 2023)].copy()

print(f"Author EUR/USD 2023 trades: {len(auth_eur23)} | Winrate: {(auth_eur23['Result']=='TP').mean()*100:.1f}% | Total R: {auth_eur23['pnl_R'].sum():.2f}")
print(f"Bot EUR/USD 2023 trades:    {len(bot_eur23)} | Winrate: {(bot_eur23['result']=='TP').mean()*100:.1f}% | Total R: {bot_eur23['rr'].sum():.2f}")

# Cross-reference dates
auth_dates = set(auth_eur23['Date'])
bot_dates = set(bot_eur23['date'])

common_dates = auth_dates.intersection(bot_dates)
bot_only = bot_dates - auth_dates
auth_only = auth_dates - bot_dates

print(f"\nCommon trade dates: {len(common_dates)}")
print(f"Dates where Bot entered but Author SKIPPED: {len(bot_only)}")
print(f"Dates where Author entered but Bot missed: {len(auth_only)}")

print("\n--- RESULTS ON DATES WHERE BOTH TRADED ---")
for d in sorted(common_dates):
    a_row = auth_eur23[auth_eur23['Date'] == d].iloc[0]
    b_row = bot_eur23[bot_eur23['date'] == d].iloc[0]
    print(f"Date: {d} | Author: {a_row['Session']} {a_row['Position']} -> {a_row['Result']} (RR={a_row['RR']}) | Bot: {b_row['session']} {b_row['dir']} -> {b_row['result']} (RR={b_row['rr']})")

print("\n--- WHAT HAPPENED ON DATES WHERE BOT TRADED BUT AUTHOR SKIPPED? ---")
bot_extra = bot_eur23[bot_eur23['date'].isin(bot_only)]
print(f"Bot extra trades count: {len(bot_extra)}")
print(f"Bot extra wins: {(bot_extra['result']=='TP').sum()}, losses: {(bot_extra['result']=='SL').sum()}")
print(f"Bot extra Winrate: {(bot_extra['result']=='TP').mean()*100:.1f}%")
print(f"Bot extra PnL: {bot_extra['rr'].sum():.2f} R")
