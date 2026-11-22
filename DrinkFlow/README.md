# DrinkFlow

DrinkFlow is a single-store management app for wholesale and retail beverage sales. It uses plain HTML, CSS and JavaScript, a Python server and a single SQLite database. It starts with an empty store and needs no package installations, database server, cloud account or subscription for local use.

## Start on a store computer

Use Python 3.11 or newer and a current browser. Extract the ZIP and open a terminal in the DrinkFlow folder. On Windows, replace `python3` with `py -3`.

```sh
python3 server.py setup owner
python3 server.py serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) on that computer. Sign in as `owner` using the password you entered. Keep the server process running during store hours. Choose an alternative port with `serve --port 8080`.

1. In **Settings**, set your store name and the default number of LRD per USD. Create separate staff accounts if several people use the app.
2. In **Inventory**, add each product. Give it a unique SKU or barcode and enter retail and wholesale prices in LRD for every selling unit. The first unit is one stock item. A 24-bottle carton is an additional unit with 24 items.
3. Use **Opening stock** to enter your first physical count and historical costs. Record starting cash, mobile money and bank balances under **Cash & Accounts → Add funds**. Use a dated transaction and a separate entry for each account and currency.
4. Add suppliers and any customers who need credit in **Contacts**. Accounts staff can set a credit limit in LRD. A credit limit of zero permits an unlimited balance. Walk-in sales must be paid in full.
5. Post a sample product purchase and sale in a separate test database if you need practice. To start that test safely, point `DRINKFLOW_DATA` at an empty test directory before running setup.

## Daily workflows

| Screen | Work |
|---|---|
| Dashboard | Review daily, weekly and monthly net billed sales, retail and wholesale split, cash balances, profit, purchases, recent records, best sellers, low stock and expiring lots. |
| Sales | Add items by catalog or barcode scanner in keyboard mode. Select the selling unit, wholesale or retail, currency and payment method. Record small discounts and tax amounts when applicable. View and print the invoice. |
| Purchases | Create a purchase order or receive goods directly. Choose the supplier, batch, expiry, unit cost and transport cost. Track partial payments. A purchase order can be received once. Enter a separate order or direct receipt for an additional delivery. |
| Expenditure | Record the date, category, amount, currency and payment method. Review the breakdown by category in Reports. |
| Inventory | Edit product records, check lot balances, record opening stock and post gains or write-offs. Use the stock report for opening, incoming, outgoing and closing quantities with the date range you choose. |
| Contacts | Record customer and supplier details, view statements and identify open or overdue invoices in Reports. Open the original document to record a later payment. |
| Reports | Choose today, this week, this month, this year or a date range. Filter the relevant reports by contact, product or staff. Use Export CSV for Excel or Print / PDF for a printable copy. |
| Cash & Accounts | Record additions and withdrawals and save each closing count after the day's posting. The report shows opening and closing book balances and the variance at the time of the count. A later transaction can change the current book expectation. |

Open a posted sale, purchase or expense and use **Reverse** if the whole transaction needs to be corrected. The reversal records who performed it, the reason, and the return or refund dated today. A sale return puts every returned item back into its original batch. For damaged items, follow with an inventory write-off. A purchase return requires all of the original quantities to still be available. To record an exchange, return the original sale and enter the replacement as a new sale. Full-document reversals and single-currency payments are the supported workflows.

## How the numbers work

- Revenue, expenses, gross profit, net operating profit and inventory valuation are reported in LRD. Each foreign currency document stores the exchange rate used when it was posted. The receipt and account balances display the currency of the actual transaction. Apply payment to an invoice in its original currency. Cash, bank and mobile money are recorded manually.
- Quantities are stored in base units. A 24-bottle carton removes 24 bottles. Sales use the earliest unexpired dated batch first, then undated stock in receipt order. Inventory valuation uses each receipt's actual lot cost, including allocated transport. Expired lots remain on hand until written off and are excluded from available sales stock.
- Gross profit is net sales excluding entered tax less the cost of goods sold. Net operating profit also deducts operating expenditure and stock write-offs. Posting a purchase increases stock value. Its cost reaches profit and loss as stock is sold or written off. Cash counts measure cash activity separately from profit.
- Inventory movements post on the current store date. The default business date is Liberia time. Posted transactions stay in the journal. Stock, payments and document numbers commit together. Duplicate submissions use one transaction number.
- This app tracks store operations and balances. Its tax field accepts the amount you supply. It does not calculate filing requirements, send invoices externally, process payments or integrate with banks.

## Staff permissions

| Role | Access |
|---|---|
| Owner | All modules, staff and settings, database backups and audit history. |
| Manager | Store operations, reports, accounts and reversals. |
| Cashier | Sales, customer details and sale payments. |
| Inventory Officer | Products, lots, stock adjustments, supplier contacts and purchases. |
| Accountant | Reports, contacts, expenditure, balances, payments, closing counts and reversals. |

## Backup and recovery

The server saves a verified SQLite snapshot at startup if it has none for the current day, then checks every five minutes for the next day's backup. It keeps snapshots for 30 days. Use **Settings → Download backup** for an on-demand copy or run `python3 server.py backup`. Keep a copy outside the store computer. A downloaded snapshot includes private business records.

By default, the application stores the database and backups in the `data` subfolder. Set the `DRINKFLOW_DATA` environment variable to use a different directory on a local disk. The recovery command requires an empty target location, verifies the source and clears old sessions:

```sh
DRINKFLOW_DATA=/path/to/restored-data python3 server.py restore /path/to/drinkflow-snapshot.sqlite3
DRINKFLOW_DATA=/path/to/restored-data python3 server.py serve --port 8080
```

On Windows PowerShell, set the variable first with `$env:DRINKFLOW_DATA = 'C:\DrinkFlow-Restored'`. Sign in and verify recent invoices, counts and stock totals in the restored instance. After verification, stop both instances before switching your normal service to the restored directory. If the owner password is lost, use `python3 server.py reset-password owner` on the store computer.

## Shared access and launch check

The app runs on one store computer by default and listens on loopback. For access from other devices, install it on one always-on host with a local persistent disk. Run the server under a dedicated operating-system account and a process supervisor that starts it on boot. Put a trusted HTTPS reverse proxy in front, enable `serve --secure-cookie` and restrict network access to the staff who need it. Run one server process against the SQLite database. Do not place a live database on a network share or a sync folder.

For example, on a host with an existing HTTPS Caddy setup you can use this Caddy site block with your assigned hostname:

```caddyfile
your-store-hostname.example {
    request_body {
        max_size 128KB
    }
    reverse_proxy 127.0.0.1:8000
}
```

Before entering live records, sign in on the devices you will use, post a test sale in a separate test database, print on your receipt printer and restore a backup. If using shared access, confirm HTTPS and staff permissions at the installed address. The ZIP includes source and tests. It is not a hosted service.

## Tests

Run the HTTP and database integration suite:

```sh
python3 -m unittest discover -s tests -v
```

The tests use temporary databases and cover checkout, stock conversion, credit, expense reporting, returns, access restrictions, competing cashiers, duplicate submissions and backup restoration. An optional Playwright browser test covers desktop checkout, CSV download, receipt PDF and narrow viewport layouts. Install Playwright in a test environment, set `DRINKFLOW_BROWSER` to a compatible Chromium executable, then rerun the same command. Browser and printer acceptance still needs to be completed on the target devices.
