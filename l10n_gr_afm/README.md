# Greece - AFM Lookup (`l10n_gr_afm`)

Odoo 19 module that fetches business registry data from the Greek AADE (Independent Authority for Public Revenue) using a partner's VAT number (AFM).

## What It Does

- Adds a **"Fetch from AADE"** button on partner contacts with a Greek VAT
- Calls the AADE RgWsPublic2 SOAP web service to retrieve official business data
- Shows a **preview wizard** with the fetched data before applying changes
- Populates partner fields: name, address, Tax Office (DOY), and primary activity code (KAD)
- Warns if the VAT number is **inactive (deactivated)**
- Logs each lookup in the partner's chatter as a quiet internal note

## Requirements

- **Odoo 19** with Greek localization (`l10n_gr`) installed
- **AADE special access codes** (not regular TAXISnet credentials)

### Getting AADE Credentials

1. Log in at https://www1.aade.gr/sgsisapps/tokenservices/protected/displayConsole.htm
2. Navigate to "Available Services"
3. Create access codes for "Αναζήτηση Βασικών Στοιχείων Μητρώου Επιχειρήσεων"

> **Note:** AADE enforces daily and monthly call limits per user. Each lookup also notifies the searched entity with your AFM and name.

## Installation

Copy this module into your Odoo addons path and install it:

```bash
odoo-bin -d <dbname> -i l10n_gr_afm --stop-after-init
```

**Dependencies:** `base_vat`, `l10n_gr`, `sales_team`

No external Python packages required (uses `requests` and `lxml` shipped with Odoo).

## Configuration

**Settings > Accounting > AADE AFM Lookup** (visible when company country is Greece)

Enter your AADE Username and Password. These are stored per-company, so multi-company setups can use different credentials.

## Usage

1. Open a contact with a Greek VAT number (or type one starting with `EL` or a bare 9-digit AFM)
2. Click **"Fetch from AADE"** next to the VAT field
3. Review the fetched data in the preview wizard
4. Click **"Apply"** to update the partner, or **"Cancel"** to discard

The button is available to **Sales** and **Accounting** users.

### Fields Populated

| From AADE | To Partner |
|-----------|-----------|
| Legal name | Name |
| Street + Number | Street |
| Postal code | Zip |
| City | City |
| Tax office (DOY) | Tax Office (DOY) |
| Primary activity code | Main Activity Code (KAD) |
| Primary activity description | Main Activity Description |

Additional fields are shown in the wizard for reference but not applied: legal form, VAT status, entity type, business status, and registration date.

### Greek Tax Info Tab

A new **"Greek Tax Info"** tab appears on Greek partner forms with editable fields for DOY and KAD. These can be filled manually or via the AADE lookup.

## AADE Service Details

- **Endpoint:** `https://www1.gsis.gr/wsaade/RgWsPublic2/RgWsPublic2`
- **Protocol:** SOAP 1.2 over TLS 1.2
- **Authentication:** WS-Security UsernameToken

The connection timeout defaults to 30 seconds and can be adjusted via the system parameter `l10n_gr_afm.aade_timeout`.

## Compatibility

- Works alongside Odoo's built-in **VIES VAT validation** (`base_vat`) without conflicts
- Supports **multi-company** with per-company credentials
- **Optional `mail` integration:** logs lookups in chatter when the `mail` module is installed, works without it

## License

LGPL-3
