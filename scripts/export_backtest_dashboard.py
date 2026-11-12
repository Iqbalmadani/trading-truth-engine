"""
Export Backtest Dashboard — Trading Truth Engine

Reads an isolated SQLite backtest database, computes key metrics (Win Rate,
Profit Factor, Max Drawdown), and exports a premium, responsive, interactive
dark-mode HTML dashboard containing the Equity Curve and detailed Trade Log.
"""

import sys
import sqlite3
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

def calculate_metrics(trades: List[Dict]) -> Tuple[Dict, List[Dict]]:
    """
    Computes professional trading statistics.
    Returns a dict of summary stats and a list of trade data points for charting.
    """
    resolved_trades = [t for t in trades if t["outcome"] in ("TP_HIT", "SL_HIT")]
    total_trades = len(trades)
    resolved_count = len(resolved_trades)
    
    if resolved_count == 0:
        return {
            "total_trades": total_trades,
            "resolved_trades": 0,
            "pending_trades": total_trades,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "won_count": 0,
            "lost_count": 0,
            "start_time": "N/A",
            "end_time": "N/A",
        }, []

    # Format timestamps
    def fmt_ts(ts):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "N/A"

    start_time = fmt_ts(trades[0]["candle_ts"])
    end_time = fmt_ts(trades[-1]["candle_ts"])

    # Basic tallies
    won_trades = [t for t in resolved_trades if t["pnl_percent"] > 0]
    lost_trades = [t for t in resolved_trades if t["pnl_percent"] <= 0]
    
    won_count = len(won_trades)
    lost_count = len(lost_trades)
    
    win_rate = (won_count / resolved_count) * 100.0 if resolved_count > 0 else 0.0
    
    # Profit Factor: sum(gains) / abs(sum(losses))
    sum_gains = sum(t["pnl_percent"] for t in won_trades)
    sum_losses = sum(t["pnl_percent"] for t in lost_trades)
    
    if abs(sum_losses) < 1e-9:
        profit_factor = sum_gains if sum_gains > 0 else 1.0
    else:
        profit_factor = sum_gains / abs(sum_losses)

    # Walk equity curve (Compounding)
    initial_capital = 1000.0
    capital = initial_capital
    equity_points = [{
        "trade_index": 0,
        "timestamp": start_time,
        "entry_price": 0.0,
        "exit_price": 0.0,
        "pnl_percent": 0.0,
        "outcome": "START",
        "equity": capital,
        "drawdown": 0.0
    }]
    
    peak = initial_capital
    max_dd = 0.0
    
    for idx, t in enumerate(resolved_trades, 1):
        pnl = t["pnl_percent"] or 0.0
        if "account_equity" in t and t["account_equity"] is not None:
            capital = t["account_equity"]
        else:
            capital = capital * (1.0 + pnl / 100.0)
        
        if capital > peak:
            peak = capital
        
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            
        equity_points.append({
            "trade_index": idx,
            "timestamp": fmt_ts(t["candle_ts"]),
            "entry_price": t["entry_price"],
            "exit_price": t["outcome_price"] or t["entry_price"],
            "pnl_percent": pnl,
            "outcome": t["outcome"],
            "equity": round(capital, 2),
            "drawdown": round(dd, 2)
        })

    total_pnl = ((capital - initial_capital) / initial_capital) * 100.0
    avg_pnl = sum(t["pnl_percent"] for t in resolved_trades) / resolved_count

    stats = {
        "total_trades": total_trades,
        "resolved_trades": resolved_count,
        "pending_trades": total_trades - resolved_count,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl, 2),
        "won_count": won_count,
        "lost_count": lost_count,
        "start_time": start_time,
        "end_time": end_time,
    }

    return stats, equity_points

def generate_html_report(
    exchange: str,
    pair: str,
    timeframe: str,
    stats: Dict,
    equity_points: List[Dict],
    all_trades: List[Dict],
    output_path: Path
):
    """Generates a premium dark-mode dashboard HTML page."""
    
    # Serialize data for Javascript injection
    equity_json = json.dumps(equity_points)
    
    # Process all trades for display
    def fmt_ts(ts):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "N/A"
            
    trades_formatted = []
    for idx, t in enumerate(all_trades, 1):
        trades_formatted.append({
            "id": t["id"],
            "candle_index": t["candle_index"],
            "date": fmt_ts(t["candle_ts"]),
            "type": t["signal_type"],
            "entry": t["entry_price"],
            "sl": t.get("stop_loss") or 0.0,
            "tp": t.get("take_profit") or 0.0,
            "leverage": t.get("leverage") or 1.0,
            "outcome": t["outcome"],
            "exit": t.get("outcome_price") or 0.0,
            "pnl": round(t.get("pnl_percent") or 0.0, 2) if t.get("pnl_percent") is not None else 0.0,
            "sl_mult": t.get("sl_multiplier", 1.0),
            "tp_mult": t.get("tp_multiplier", 1.0),
            "lev_mult": t.get("lev_multiplier", 1.0),
        })
    trades_json = json.dumps(trades_formatted)

    # Color grading variables
    pnl_class = "text-green" if stats["total_pnl"] >= 0 else "text-red"
    pnl_sign = "+" if stats["total_pnl"] >= 0 else ""
    
    pf_rating = "Average"
    pf_color = "text-yellow"
    if stats["profit_factor"] >= 2.0:
        pf_rating = "Excellent"
        pf_color = "text-green"
    elif stats["profit_factor"] >= 1.5:
        pf_rating = "Good"
        pf_color = "text-green"
    elif stats["profit_factor"] < 1.0:
        pf_rating = "Unprofitable"
        pf_color = "text-red"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TTE Backtest Dashboard — {exchange.upper()} {pair} {timeframe}</title>
    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <!-- Chart.js CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: rgba(22, 33, 54, 0.6);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --green: #10b981;
            --green-glow: rgba(16, 185, 129, 0.15);
            --red: #ef4444;
            --red-glow: rgba(239, 68, 68, 0.15);
            --yellow: #f59e0b;
            --blue: #3b82f6;
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        
        body {{
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            padding: 2rem 1.5rem;
            line-height: 1.5;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        /* Header styling */
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
        }}
        
        .title-area h1 {{
            font-size: 2.25rem;
            font-weight: 700;
            letter-spacing: -0.025em;
            background: linear-gradient(135deg, #fff 30%, var(--text-muted) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        
        .title-area p {{
            color: var(--text-muted);
            margin-top: 0.25rem;
            font-size: 0.95rem;
        }}
        
        .badge-meta {{
            background: rgba(59, 130, 246, 0.1);
            color: var(--blue);
            border: 1px solid rgba(59, 130, 246, 0.2);
            padding: 0.35rem 0.85rem;
            border-radius: 9999px;
            font-weight: 500;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        
        /* Cards Layout */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2.5rem;
        }}
        
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            padding: 1.5rem;
            backdrop-filter: blur(12px);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }}
        
        .card:hover {{
            transform: translateY(-4px);
            border-color: rgba(255, 255, 255, 0.15);
            box-shadow: 0 10px 20px -10px rgba(0,0,0,0.5);
        }}
        
        .card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 3px;
            background: transparent;
        }}
        
        .card-pnl::before {{ background: var(--green); }}
        .card-wr::before {{ background: var(--blue); }}
        .card-pf::before {{ background: var(--yellow); }}
        .card-dd::before {{ background: var(--red); }}
        
        .card-label {{
            font-size: 0.85rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 500;
        }}
        
        .card-value {{
            font-size: 2rem;
            font-weight: 700;
            margin-top: 0.5rem;
            line-height: 1.2;
        }}
        
        .card-subtext {{
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 0.5rem;
            display: flex;
            align-items: center;
            gap: 0.25rem;
        }}
        
        /* Chart container */
        .chart-container {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            padding: 1.75rem;
            backdrop-filter: blur(12px);
            margin-bottom: 2.5rem;
        }}
        
        .chart-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }}
        
        .chart-header h2 {{
            font-size: 1.25rem;
            font-weight: 600;
        }}
        
        .chart-meta {{
            font-size: 0.85rem;
            color: var(--text-muted);
        }}
        
        /* Table container */
        .trades-container {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            padding: 1.75rem;
            backdrop-filter: blur(12px);
        }}
        
        .table-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            flex-wrap: wrap;
            gap: 1rem;
        }}
        
        .table-header h2 {{
            font-size: 1.25rem;
            font-weight: 600;
        }}
        
        .filter-group {{
            display: flex;
            gap: 0.75rem;
            align-items: center;
        }}
        
        .search-input {{
            background: rgba(0,0,0,0.25);
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            padding: 0.5rem 0.75rem;
            color: var(--text-main);
            font-family: inherit;
            font-size: 0.9rem;
            outline: none;
            transition: border-color 0.2s;
        }}
        
        .search-input:focus {{
            border-color: var(--blue);
        }}
        
        .filter-btn {{
            background: rgba(255,255,255,0.04);
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            padding: 0.4rem 0.8rem;
            border-radius: 0.5rem;
            font-size: 0.85rem;
            cursor: pointer;
            font-family: inherit;
            transition: all 0.2s;
        }}
        
        .filter-btn.active, .filter-btn:hover {{
            background: rgba(255,255,255,0.1);
            color: var(--text-main);
            border-color: rgba(255,255,255,0.25);
        }}
        
        .table-wrapper {{
            overflow-x: auto;
            max-height: 500px;
            overflow-y: auto;
            border-radius: 0.5rem;
            border: 1px solid rgba(255,255,255,0.04);
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.9rem;
        }}
        
        th {{
            background: rgba(0, 0, 0, 0.4);
            padding: 0.85rem 1rem;
            font-weight: 600;
            color: var(--text-muted);
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        
        td {{
            padding: 0.85rem 1rem;
            border-bottom: 1px solid rgba(255,255,255,0.03);
        }}
        
        tr:hover td {{
            background: rgba(255,255,255,0.02);
        }}
        
        /* Badges */
        .badge {{
            padding: 0.2rem 0.5rem;
            border-radius: 0.25rem;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            display: inline-block;
        }}
        
        .badge-long {{ background: rgba(16, 185, 129, 0.1); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.2); }}
        .badge-short {{ background: rgba(239, 68, 68, 0.1); color: var(--red); border: 1px solid rgba(239, 68, 68, 0.2); }}
        
        .badge-tp {{ background: var(--green-glow); color: var(--green); }}
        .badge-sl {{ background: var(--red-glow); color: var(--red); }}
        .badge-pending {{ background: rgba(245, 158, 11, 0.1); color: var(--yellow); }}
        
        /* Helper Utility Classes */
        .text-green {{ color: var(--green) !important; }}
        .text-red {{ color: var(--red) !important; }}
        .text-yellow {{ color: var(--yellow) !important; }}
        .text-blue {{ color: var(--blue) !important; }}
        
        /* Scrollbar styling */
        .table-wrapper::-webkit-scrollbar {{
            width: 6px;
            height: 6px;
        }}
        .table-wrapper::-webkit-scrollbar-track {{
            background: rgba(0,0,0,0.1);
        }}
        .table-wrapper::-webkit-scrollbar-thumb {{
            background: rgba(255,255,255,0.1);
            border-radius: 3px;
        }}
        .table-wrapper::-webkit-scrollbar-thumb:hover {{
            background: rgba(255,255,255,0.25);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-area">
                <h1>Trading Truth Engine</h1>
                <p>Backtest Performance Report &mdash; {stats["start_time"]} to {stats["end_time"]}</p>
            </div>
            <div>
                <span class="badge-meta">{exchange} / {pair} / {timeframe}</span>
            </div>
        </header>
        
        <!-- Stats Cards Section -->
        <section class="metrics-grid">
            <!-- Total return -->
            <div class="card card-pnl">
                <p class="card-label">Total Return</p>
                <p class="card-value {pnl_class}">{pnl_sign}{stats["total_pnl"]}%</p>
                <div class="card-subtext">
                    <span>Compounded from $1,000</span>
                </div>
            </div>
            
            <!-- Win rate -->
            <div class="card card-wr">
                <p class="card-label">Win Rate</p>
                <p class="card-value text-blue">{stats["win_rate"]}%</p>
                <div class="card-subtext">
                    <span>{stats["won_count"]} wins / {stats["lost_count"]} losses</span>
                </div>
            </div>
            
            <!-- Profit Factor -->
            <div class="card card-pf">
                <p class="card-label">Profit Factor</p>
                <p class="card-value {pf_color}">{stats["profit_factor"]}</p>
                <div class="card-subtext">
                    <span>Rating: <strong>{pf_rating}</strong></span>
                </div>
            </div>
            
            <!-- Max Drawdown -->
            <div class="card card-dd">
                <p class="card-label">Max Drawdown</p>
                <p class="card-value text-red">-{stats["max_drawdown"]}%</p>
                <div class="card-subtext">
                    <span>Peak-to-Trough Decline</span>
                </div>
            </div>
        </section>
        
        <!-- Equity Curve Chart -->
        <section class="chart-container">
            <div class="chart-header">
                <h2>Cumulative Equity Curve</h2>
                <span class="chart-meta">compounding return (%) over resolved trades</span>
            </div>
            <div style="position: relative; height: 350px; width: 100%;">
                <canvas id="equityChart"></canvas>
            </div>
        </section>
        
        <!-- Trade Log Table -->
        <section class="trades-container">
            <div class="table-header">
                <h2>Executed Trade Log ({stats["total_trades"]} total, {stats["pending_trades"]} pending)</h2>
                <div class="filter-group">
                    <input type="text" id="searchInput" placeholder="Search pair, outcome, type..." class="search-input" onkeyup="filterTable()">
                    <button class="filter-btn active" onclick="filterOutcome('ALL', this)">All</button>
                    <button class="filter-btn" onclick="filterOutcome('TP_HIT', this)">TP Hits</button>
                    <button class="filter-btn" onclick="filterOutcome('SL_HIT', this)">SL Hits</button>
                    <button class="filter-btn" onclick="filterOutcome('PENDING', this)">Pending</button>
                </div>
            </div>
            
            <div class="table-wrapper">
                <table id="tradesTable">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Date (UTC)</th>
                            <th>Type</th>
                            <th>Entry Price</th>
                            <th>SL / TP</th>
                            <th>Leverage</th>
                            <th>Outcome</th>
                            <th>Exit Price</th>
                            <th>P&L %</th>
                            <th>Multipliers (SL/TP/Lev)</th>
                        </tr>
                    </thead>
                    <tbody id="tableBody">
                        <!-- Populated by JS -->
                    </tbody>
                </table>
            </div>
        </section>
    </div>
    
    <script>
        // Injected JSON data
        const equityData = {equity_json};
        const tradesData = {trades_json};
        
        // Render Chart.js
        document.addEventListener('DOMContentLoaded', function() {{
            const ctx = document.getElementById('equityChart').getContext('2d');
            
            const labels = equityData.map(d => `Trade ${{d.trade_index}}`);
            const dataPoints = equityData.map(d => d.equity);
            
            const isProfitable = dataPoints[dataPoints.length - 1] >= 1000;
            const lineColor = isProfitable ? '#10b981' : '#ef4444';
            const gradientColor = isProfitable ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)';
            
            const gradient = ctx.createLinearGradient(0, 0, 0, 300);
            gradient.addColorStop(0, gradientColor);
            gradient.addColorStop(1, 'rgba(11, 15, 25, 0)');

            new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: labels,
                    datasets: [{{
                        label: 'Equity ($)',
                        data: dataPoints,
                        borderColor: lineColor,
                        borderWidth: 2.5,
                        backgroundColor: gradient,
                        fill: true,
                        tension: 0.2,
                        pointBackgroundColor: lineColor,
                        pointRadius: function(context) {{
                            const index = context.dataIndex;
                            return index === 0 || index === dataPoints.length - 1 ? 5 : 2;
                        }},
                        pointHoverRadius: 6
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{
                            display: false
                        }},
                        tooltip: {{
                            backgroundColor: 'rgba(15, 23, 42, 0.95)',
                            titleColor: '#f1f5f9',
                            bodyColor: '#94a3b8',
                            borderColor: 'rgba(255, 255, 255, 0.1)',
                            borderWidth: 1,
                            padding: 10,
                            displayColors: false,
                            callbacks: {{
                                label: function(context) {{
                                    const pt = equityData[context.dataIndex];
                                    let lines = [`Balance: $${{pt.equity}}`];
                                    if (pt.outcome !== 'START') {{
                                        lines.push(`Outcome: ${{pt.outcome}}`);
                                        lines.push(`Trade PnL: ${{pt.pnl_percent > 0 ? '+' : ''}}${{pt.pnl_percent}}%`);
                                        lines.push(`Drawdown: ${{pt.drawdown}}%`);
                                    }}
                                    return lines;
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{
                            grid: {{
                                color: 'rgba(255, 255, 255, 0.03)',
                                borderColor: 'rgba(255, 255, 255, 0.05)'
                            }},
                            ticks: {{
                                color: '#64748b',
                                font: {{
                                    family: 'Outfit'
                                }},
                                maxTicksLimit: 12
                            }}
                        }},
                        y: {{
                            grid: {{
                                color: 'rgba(255, 255, 255, 0.03)',
                                borderColor: 'rgba(255, 255, 255, 0.05)'
                            }},
                            ticks: {{
                                color: '#64748b',
                                font: {{
                                    family: 'Outfit'
                                }},
                                callback: function(value) {{
                                    return '$' + value;
                                }}
                            }}
                        }}
                    }}
                }}
            }});
            
            // Build Table
            buildTable(tradesData);
        }});
        
        function buildTable(data) {{
            const tbody = document.getElementById('tableBody');
            tbody.innerHTML = '';
            
            if (data.length === 0) {{
                tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 2rem;">No trades generated in this backtest.</td></tr>`;
                return;
            }}
            
            data.forEach((t, index) => {{
                const typeBadge = t.type === 'LONG' ? 'badge-long' : 'badge-short';
                
                let outcomeClass = 'badge-pending';
                if (t.outcome === 'TP_HIT') outcomeClass = 'badge-tp';
                if (t.outcome === 'SL_HIT') outcomeClass = 'badge-sl';
                
                const pnlSign = t.pnl > 0 ? '+' : '';
                const pnlClass = t.pnl > 0 ? 'text-green' : (t.pnl < 0 ? 'text-red' : '');
                
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${{index + 1}}</td>
                    <td>${{t.date}}</td>
                    <td><span class="badge ${{typeBadge}}">${{t.type}}</span></td>
                    <td style="font-weight: 500;">${{t.entry.toFixed(4)}}</td>
                    <td>
                        <div style="font-size: 0.8rem; color: var(--text-muted);">SL: ${{t.sl.toFixed(4)}}</div>
                        <div style="font-size: 0.8rem; color: var(--text-muted);">TP: ${{t.tp.toFixed(4)}}</div>
                    </td>
                    <td>${{t.leverage.toFixed(1)}}x</td>
                    <td><span class="badge ${{outcomeClass}}">${{t.outcome}}</span></td>
                    <td style="font-weight: 500;">${{t.exit > 0 ? t.exit.toFixed(4) : '—'}}</td>
                    <td class="${{pnlClass}}" style="font-weight: 600;">${{t.outcome !== 'PENDING' ? pnlSign + t.pnl.toFixed(2) + '%' : '—'}}</td>
                    <td style="font-size: 0.8rem; color: var(--text-muted);">
                        ${{t.sl_mult.toFixed(2)}} / ${{t.tp_mult.toFixed(2)}} / ${{t.lev_mult.toFixed(1)}}x
                    </td>
                `;
                tbody.appendChild(row);
            }});
        }}
        
        let currentFilter = 'ALL';
        
        function filterOutcome(outcome, btn) {{
            // Toggle active button
            const buttons = document.querySelectorAll('.filter-btn');
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            currentFilter = outcome;
            applyFilters();
        }}
        
        function filterTable() {{
            applyFilters();
        }}
        
        function applyFilters() {{
            const searchVal = document.getElementById('searchInput').value.toLowerCase();
            
            const filtered = tradesData.filter(t => {{
                // Outcome filter
                const matchesOutcome = currentFilter === 'ALL' || t.outcome === currentFilter;
                
                // Search filter
                const matchesSearch = 
                    t.date.toLowerCase().includes(searchVal) ||
                    t.type.toLowerCase().includes(searchVal) ||
                    t.outcome.toLowerCase().includes(searchVal) ||
                    t.entry.toString().includes(searchVal);
                    
                return matchesOutcome && matchesSearch;
            }});
            
            buildTable(filtered);
        }}
    </script>
</body>
</html>
"""
    output_path.write_text(html_content, encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="TTE Backtest Dashboard Exporter")
    parser.add_argument("--db", type=str, required=True, help="Path to backtest SQLite DB file")
    parser.add_argument("--out", type=str, default=None, help="Output dashboard HTML filepath")
    args = parser.parse_args()
    
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: Backtest database file '{db_path}' does not exist.")
        sys.exit(1)
        
    # Resolve exchange, pair, tf from filename if possible
    # e.g., backtest_gateio_ETH_USDT_1h.db -> exchange=gateio, pair=ETH_USDT, timeframe=1h
    filename_parts = db_path.stem.split("_")
    if len(filename_parts) >= 4 and filename_parts[0] == "backtest":
        exchange = filename_parts[1]
        timeframe = filename_parts[-1]
        pair = "_".join(filename_parts[2:-1])
    else:
        exchange = "unknown"
        pair = "unknown"
        timeframe = "unknown"
        
    if args.out:
        output_path = Path(args.out)
    else:
        output_path = db_path.parent / f"dashboard_{exchange}_{pair}_{timeframe}.html"
        
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    
    try:
        trades = conn.execute("SELECT * FROM bt_signals ORDER BY candle_index").fetchall()
        trades = [dict(r) for r in trades]
    except sqlite3.OperationalError as e:
        print(f"Error reading backtest signals table: {e}")
        conn.close()
        sys.exit(1)
        
    conn.close()
    
    stats, equity_points = calculate_metrics(trades)
    
    generate_html_report(
        exchange=exchange,
        pair=pair,
        timeframe=timeframe,
        stats=stats,
        equity_points=equity_points,
        all_trades=trades,
        output_path=output_path
    )
    
    print("\n" + "="*50)
    print("  TTE BACKTEST REPORT EXPORTED SUCCESSFULLY")
    print("="*50)
    print(f"  Exchange       : {exchange}")
    print(f"  Pair           : {pair}")
    print(f"  Timeframe      : {timeframe}")
    print(f"  Period         : {stats['start_time']} to {stats['end_time']}")
    print(f"  Total Trades   : {stats['total_trades']} ({stats['resolved_trades']} resolved, {stats['pending_trades']} pending)")
    print(f"  Win Rate       : {stats['win_rate']}%")
    print(f"  Profit Factor  : {stats['profit_factor']}")
    print(f"  Max Drawdown   : -{stats['max_drawdown']}%")
    print(f"  Total Return   : {stats['total_pnl']}%")
    print(f"  Average P&L    : {stats['avg_pnl']}%")
    print(f"  Saved Dashboard: {output_path.absolute()}")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()
