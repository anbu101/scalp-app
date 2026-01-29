/* -------------------------
   CSV Export Utility
-------------------------- */

export function exportToCSV(data, filename = 'export.csv') {
    if (!data || data.length === 0) {
      console.warn('No data to export');
      return;
    }
  
    // Get headers from first object
    const headers = Object.keys(data[0]);
    
    // Create CSV content
    const csvContent = [
      headers.join(','), // Header row
      ...data.map(row => 
        headers.map(header => {
          const value = row[header];
          // Handle values with commas, quotes, or newlines
          if (value === null || value === undefined) return '';
          const stringValue = String(value);
          if (stringValue.includes(',') || stringValue.includes('"') || stringValue.includes('\n')) {
            return `"${stringValue.replace(/"/g, '""')}"`;
          }
          return stringValue;
        }).join(',')
      )
    ].join('\n');
  
    // Create blob and download
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    downloadBlob(blob, filename);
  }
  
  /* -------------------------
     Excel Export Utility (TSV format)
  -------------------------- */
  
  export function exportToExcel(data, filename = 'export.xlsx') {
    if (!data || data.length === 0) {
      console.warn('No data to export');
      return;
    }
  
    // Get headers
    const headers = Object.keys(data[0]);
    
    // Create TSV content (Excel opens TSV files)
    const tsvContent = [
      headers.join('\t'),
      ...data.map(row => 
        headers.map(header => {
          const value = row[header];
          return value === null || value === undefined ? '' : String(value);
        }).join('\t')
      )
    ].join('\n');
  
    // Create blob with Excel MIME type
    const blob = new Blob([tsvContent], { type: 'application/vnd.ms-excel' });
    downloadBlob(blob, filename);
  }
  
  /* -------------------------
     JSON Export Utility
  -------------------------- */
  
  export function exportToJSON(data, filename = 'export.json') {
    if (!data) {
      console.warn('No data to export');
      return;
    }
  
    const jsonContent = JSON.stringify(data, null, 2);
    const blob = new Blob([jsonContent], { type: 'application/json' });
    downloadBlob(blob, filename);
  }
  
  /* -------------------------
     Download Helper
  -------------------------- */
  
  function downloadBlob(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  }
  
  /* -------------------------
     Format Trade Data for Export (Enhanced with Individual Entries)
  -------------------------- */
  
  export function formatTradesForExport(trades) {
    return trades.map(trade => ({
      'Symbol': trade.tradingsymbol || '',
      'Side': trade.side || '',
      'Quantity': trade.quantity || trade.day_buy_quantity || 0,
      'Entry Price': trade.buy_price || trade.average_price || 0,
      'Exit Price': trade.sell_price || trade.last_price || 0,
      'P&L': trade.pnl || 0,
      'Entry Time': trade.buy_timestamp || trade.created_at || '',
      'Exit Time': trade.sell_timestamp || trade.updated_at || '',
      'Product': trade.product || '',
      'Exchange': trade.exchange || ''
    }));
  }
  
  /* -------------------------
     Format Trade Data with Individual Legs (Detailed)
  -------------------------- */
  
  export function formatDetailedTradesForExport(trades) {
    // If trades already have individual entries, use them
    const detailedTrades = [];
    
    trades.forEach(trade => {
      // Check if trade has multiple legs/entries
      if (trade.legs && Array.isArray(trade.legs)) {
        // Export each leg separately
        trade.legs.forEach((leg, index) => {
          detailedTrades.push({
            'Trade ID': `${trade.tradingsymbol}_${index + 1}`,
            'Symbol': trade.tradingsymbol || '',
            'Side': trade.side || '',
            'Leg': index + 1,
            'Total Legs': trade.legs.length,
            'Quantity': leg.quantity || 0,
            'Entry Price': leg.entry_price || leg.buy_price || 0,
            'Exit Price': leg.exit_price || leg.sell_price || 0,
            'Entry Time': leg.entry_time || leg.buy_timestamp || '',
            'Exit Time': leg.exit_time || leg.sell_timestamp || '',
            'P&L': leg.pnl || 0,
            'Status': leg.status || '',
            'Product': trade.product || '',
            'Exchange': trade.exchange || ''
          });
        });
      } else {
        // Single trade entry
        detailedTrades.push({
          'Trade ID': trade.tradingsymbol,
          'Symbol': trade.tradingsymbol || '',
          'Side': trade.side || '',
          'Leg': 1,
          'Total Legs': 1,
          'Quantity': trade.quantity || trade.day_buy_quantity || 0,
          'Entry Price': trade.buy_price || trade.average_price || 0,
          'Exit Price': trade.sell_price || trade.last_price || 0,
          'Entry Time': trade.buy_timestamp || trade.created_at || '',
          'Exit Time': trade.sell_timestamp || trade.updated_at || '',
          'P&L': trade.pnl || 0,
          'Status': trade.status || '',
          'Product': trade.product || '',
          'Exchange': trade.exchange || ''
        });
      }
    });
    
    return detailedTrades;
  }
  
  /* -------------------------
     Trade Journal Export (Most Detailed)
  -------------------------- */
  
  export function formatTradeJournalForExport(trades) {
    const journal = [];
    
    trades.forEach(trade => {
      // Entry record
      journal.push({
        'Timestamp': trade.buy_timestamp || trade.created_at || '',
        'Action': 'ENTRY',
        'Symbol': trade.tradingsymbol || '',
        'Side': trade.side || '',
        'Quantity': trade.quantity || trade.day_buy_quantity || 0,
        'Price': trade.buy_price || trade.average_price || 0,
        'Value': (trade.quantity || trade.day_buy_quantity || 0) * (trade.buy_price || trade.average_price || 0),
        'P&L': 0,
        'Cumulative P&L': trade.cumulative_pnl || 0,
        'Notes': `Entered ${trade.tradingsymbol}`
      });
      
      // Exit record (if closed)
      if (trade.sell_timestamp || trade.updated_at) {
        journal.push({
          'Timestamp': trade.sell_timestamp || trade.updated_at || '',
          'Action': 'EXIT',
          'Symbol': trade.tradingsymbol || '',
          'Side': trade.side || '',
          'Quantity': trade.quantity || trade.day_buy_quantity || 0,
          'Price': trade.sell_price || trade.last_price || 0,
          'Value': (trade.quantity || trade.day_buy_quantity || 0) * (trade.sell_price || trade.last_price || 0),
          'P&L': trade.pnl || 0,
          'Cumulative P&L': trade.cumulative_pnl || 0,
          'Notes': `Exited ${trade.tradingsymbol} - ${trade.pnl > 0 ? 'Profit' : 'Loss'}`
        });
      }
    });
    
    // Sort by timestamp
    journal.sort((a, b) => new Date(a.Timestamp) - new Date(b.Timestamp));
    
    return journal;
  }
  
  /* -------------------------
     Format Performance Summary for Export
  -------------------------- */
  
  export function formatPerformanceSummary(metrics, positions) {
    return [
      {
        'Metric': 'Total Trades',
        'Value': metrics.totalTrades || 0
      },
      {
        'Metric': 'Wins',
        'Value': metrics.wins || 0
      },
      {
        'Metric': 'Losses',
        'Value': metrics.losses || 0
      },
      {
        'Metric': 'Win Rate %',
        'Value': metrics.winRate?.toFixed(2) || '0.00'
      },
      {
        'Metric': 'Total P&L',
        'Value': Math.round(metrics.totalPnL || 0)
      },
      {
        'Metric': 'Average P&L',
        'Value': Math.round(metrics.avgPnL || 0)
      },
      {
        'Metric': 'Average Win',
        'Value': Math.round(metrics.avgWin || 0)
      },
      {
        'Metric': 'Average Loss',
        'Value': Math.round(metrics.avgLoss || 0)
      },
      {
        'Metric': 'Profit Factor',
        'Value': metrics.profitFactor === Infinity ? 'Infinity' : metrics.profitFactor?.toFixed(2) || '0.00'
      },
      {
        'Metric': 'Best Trade',
        'Value': `${metrics.bestTrade?.tradingsymbol || 'N/A'} (₹${Math.round(metrics.bestTrade?.pnl || 0)})`
      },
      {
        'Metric': 'Worst Trade',
        'Value': `${metrics.worstTrade?.tradingsymbol || 'N/A'} (₹${Math.round(metrics.worstTrade?.pnl || 0)})`
      }
    ];
  }
  
  /* -------------------------
     Generate Date-stamped Filename
  -------------------------- */
  
  export function generateFilename(prefix = 'scalp_trades', extension = 'csv') {
    const now = new Date();
    const dateStr = now.toISOString().split('T')[0]; // YYYY-MM-DD
    const timeStr = now.toTimeString().split(' ')[0].replace(/:/g, '-'); // HH-MM-SS
    return `${prefix}_${dateStr}_${timeStr}.${extension}`;
  }
  
  /* -------------------------
     Copy to Clipboard
  -------------------------- */
  
  export async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
      return false;
    }
  }
  
  /* -------------------------
     Print Report
  -------------------------- */
  
  export function printReport(title, content) {
    const printWindow = window.open('', '', 'width=800,height=600');
    
    printWindow.document.write(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>${title}</title>
        <style>
          body {
            font-family: Arial, sans-serif;
            padding: 20px;
            color: #333;
          }
          h1 {
            color: #1a1a1a;
            border-bottom: 2px solid #3b82f6;
            padding-bottom: 10px;
          }
          table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
          }
          th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
          }
          th {
            background-color: #f5f5f5;
            font-weight: bold;
          }
          .positive {
            color: #10b981;
          }
          .negative {
            color: #ef4444;
          }
          @media print {
            body {
              padding: 0;
            }
          }
        </style>
      </head>
      <body>
        ${content}
      </body>
      </html>
    `);
    
    printWindow.document.close();
    printWindow.focus();
    
    setTimeout(() => {
      printWindow.print();
      printWindow.close();
    }, 250);
  }
  
  /* -------------------------
     Usage Examples
  -------------------------- */
  
  /*
  
  // In Dashboard or Analytics page:
  
  import { 
    exportToCSV, 
    exportToExcel, 
    formatTradesForExport,
    formatPerformanceSummary,
    generateFilename,
    copyToClipboard,
    printReport
  } from '../utils/export';
  
  // Export trades to CSV
  function handleExportCSV() {
    const formattedTrades = formatTradesForExport(positions.closed);
    exportToCSV(formattedTrades, generateFilename('trades', 'csv'));
  }
  
  // Export trades to Excel
  function handleExportExcel() {
    const formattedTrades = formatTradesForExport(positions.closed);
    exportToExcel(formattedTrades, generateFilename('trades', 'xlsx'));
  }
  
  // Export performance summary
  function handleExportSummary() {
    const summary = formatPerformanceSummary(metrics, positions);
    exportToCSV(summary, generateFilename('performance_summary', 'csv'));
  }
  
  // Copy summary to clipboard
  async function handleCopyToClipboard() {
    const summary = formatPerformanceSummary(metrics, positions);
    const text = summary.map(row => `${row.Metric}: ${row.Value}`).join('\n');
    const success = await copyToClipboard(text);
    if (success) {
      toast.success('Copied!', 'Summary copied to clipboard');
    }
  }
  
  // Print report
  function handlePrint() {
    const content = `
      <h1>Trading Report - ${new Date().toLocaleDateString()}</h1>
      <table>
        <tr>
          <th>Metric</th>
          <th>Value</th>
        </tr>
        ${formatPerformanceSummary(metrics, positions).map(row => `
          <tr>
            <td>${row.Metric}</td>
            <td>${row.Value}</td>
          </tr>
        `).join('')}
      </table>
    `;
    printReport('Trading Performance Report', content);
  }
  
  */