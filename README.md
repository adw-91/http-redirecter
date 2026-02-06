# HTTP Redirecter

An Azure Function App that performs HTTP redirects based on the incoming hostname. Redirect mappings are stored in Azure Table Storage, making it easy to add, update, or remove redirects without redeploying.

Incoming requests are matched by hostname and redirected (307 Temporary Redirect) to the configured target URL. Path and query string are preserved.

## How it works

1. A request arrives at the Function App (e.g. `https://old.example.com/some/path?q=1`)
2. The hostname (`old.example.com`) is looked up in the `redirects` table
3. If a match is found, the client is redirected to the target URL with the original path and query string appended (e.g. `https://new.example.com/some/path?q=1`)
4. Lookups are cached in memory (default 5 minutes) to minimise storage calls

## Prerequisites

- An Azure subscription
- Azure CLI or Azure Portal access
- Python 3.12

## Setup

### 1. Create the Function App

Create an Azure Function App on your preferred SKU (Consumption, Flex Consumption, Premium, etc.) with a Storage Account. An existing storage account is fine.

```
Runtime stack: Python 3.12
OS: Linux
```

### 2. Enable Managed Identity

Enable a system-assigned Managed Identity on the Function App:

**Portal:** Function App > Identity > System assigned > On

**CLI:**
```bash
az functionapp identity assign --name <func-app-name> --resource-group <rg-name>
```

### 3. Create the redirects table

In the Storage Account attached to the Function App, create a table named **`redirects`** (or a custom name — set the `REDIRECT_TABLE_NAME` app setting to match).

Add a row for each redirect:

| Column | Value | Example |
|---|---|---|
| **PartitionKey** | The source hostname (lowercase) | `old.example.com` |
| **RowKey** | `default` | `default` |
| **RedirectUrl** | The target base URL | `https://new.example.com` |

The `RedirectUrl` value can include a scheme (`https://new.example.com`) or omit it (`www.new.example.com`) — `https://` is prepended automatically if no scheme is provided.

You can use Azure Storage Explorer, the Azure Portal, or the CLI to manage table entries.

### 4. Assign table data reader role

Grant the Function App's Managed Identity the **Storage Table Data Reader** role on the Storage Account:

```bash
az role assignment create \
  --assignee <managed-identity-principal-id> \
  --role "Storage Table Data Reader" \
  --scope /subscriptions/<sub-id>/resourceGroups/<rg-name>/providers/Microsoft.Storage/storageAccounts/<storage-name>
```

### 5. Configure identity-based storage connection

Ensure the Function App uses identity-based connection for its storage account. The following app settings should be configured (instead of a connection string):

| Setting | Value |
|---|---|
| `AzureWebJobsStorage__blobServiceUri` | `https://<storageaccount>.blob.core.windows.net` |
| `AzureWebJobsStorage__queueServiceUri` | `https://<storageaccount>.queue.core.windows.net` |
| `AzureWebJobsStorage__tableServiceUri` | `https://<storageaccount>.table.core.windows.net` |
| `AzureWebJobsStorage__credential` | `managedidentity` |

### 6. Set up a deployment pipeline

Two pipeline definitions are included:

#### GitHub Actions (`.github/workflows/deploy.yml`)

Uses OIDC (federated credentials) for authentication. Configure the following GitHub repository secrets:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | App registration client ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

Update the `AZURE_FUNCTIONAPP_NAME` variable in the workflow file.

You will also need to configure a federated credential on the app registration for your GitHub repo. See [Microsoft docs: GitHub Actions OIDC](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust?pivots=identity-wif-apps-methods-azp#github-actions).

#### Azure DevOps (`azure-deployment/ado-pipeline.yml`)

Uses an Azure service connection for authentication. Update the following variables in the pipeline file:

| Variable | Value |
|---|---|
| `azureFunctionAppName` | Your Function App name |
| `azureSubscription` | Your Azure DevOps service connection name |

Create a new pipeline in Azure DevOps and point it at `azure-deployment/ado-pipeline.yml`.

### 7. Add a custom domain

Navigate to **Function App > Settings > Custom domains** and select **Add custom domain**.

1. Enter the hostname you want to redirect (e.g. `old.example.com`).
2. Azure will generate the required DNS records for validation:
   - A **TXT** record for domain ownership verification: `asuid.old.example.com` with a domain verification ID value.
   - A **CNAME** record to route traffic: `old.example.com` -> `<func-app-name>.azurewebsites.net`.
3. Add the **TXT** record to your DNS provider first and validate ownership in the portal.
4. Optionally configure an SSL binding at this stage (see step 8).
5. When ready to go live, add the **CNAME** record in your DNS provider to activate the redirect.

For apex/root domains, an **A** record with the Function App's IP address is used instead of a CNAME. The TXT record for root domains uses `asuid` as the host name. Consider using Azure DNS if your provider doesn't support ALIAS/ANAME records for root domains.

### 8. Configure SSL

Add an SSL certificate for the custom domain via Settings > Certificate. Options include:

- **App Service Managed Certificate** (free, auto-renewed, not supported on Consumption plan)
- **Bring your own certificate** (upload a PFX/PEM or import from KV)
Note: If using KV import, the Managed Identity will require the "Azure Key Vault Certificate User" role assigning.

SSL bindings can be configured during custom domain setup (step 7) or afterwards via **Custom domains > Add binding**.

### 9. Verify

Browse to your custom domain and confirm the redirect is working:

```bash
curl -I https://old.example.com/some/path
# Expected: HTTP 307 with Location: https://new.example.com/some/path
```

## Configuration

| App Setting | Required | Default | Description |
|---|---|---|---|
| `AzureWebJobsStorage__tableServiceUri` | Yes | - | Table service endpoint (set up in step 5) |
| `REDIRECT_TABLE_NAME` | No | `redirects` | Name of the Azure Table Storage table containing redirect mappings |
| `CACHE_TTL_SECONDS` | No | `300` | How long (seconds) redirect lookups are cached in memory |

## Notes

- **Redirect type**: Uses 307 (Temporary Redirect) to preserve the HTTP method. If you need permanent redirects (301), modify the `status_code` in `function_app.py`.
- **Multiple domains**: Add one row per hostname to the `redirects` table. A single Function App can handle redirects for many domains.
- **Internal and external DNS**: Works with both. For internal-only redirects, use private DNS zones and VNet integration.
- **VNet integration**: The Function App can be VNet-integrated if the redirect targets are on private networks or if you need to restrict outbound traffic. Table Storage access can be locked down to the VNet using service endpoints or private endpoints.
- **Path and query preservation**: The full request path and query string are appended to the redirect target URL.
- **Caching**: Redirect lookups are cached in memory to reduce Table Storage calls. Cache TTL is configurable via the `CACHE_TTL_SECONDS` app setting. Negative lookups (unknown hosts) are also cached to prevent abuse.
- **Logging**: Redirect activity is logged at Information level. Application Insights sampling is enabled by default to manage costs at high traffic volumes. Adjust `host.json` to change log levels or sampling rates.
- **Security**: Redirect targets are validated to ensure they are well-formed URLs with a scheme and hostname. If no scheme is provided, `https://` is assumed. Values that don't resolve to a valid URL are rejected. The Table Storage lookup is scoped to the `redirects` table only.

## License

See [LICENSE](LICENSE).
