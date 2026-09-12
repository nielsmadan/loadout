# Publishing the documentation

The public site is configured for **https://loadoutai.dev/**. It follows Splashdown's Zensical and GitHub
Pages setup. The source repository is [nielsmadan/loadout](https://github.com/nielsmadan/loadout).

## Preview and build

From this checkout:

```sh
just docs
```

Zensical prints the local address, normally http://127.0.0.1:8000. It rebuilds when pages change.

For a strict production build:

```sh
just docs-build
```

The generated output is `site/`. Only `docs/user/` is published. This guide, implementation
research, ADRs, design documents, and source assets in `docs/assets/` stay outside the site.

The site uses Zensical 0.0.59, matching the Splashdown checkout used as the reference. Its
dependency belongs to the `docs` group and is pinned in `uv.lock`.

## Select the Pages publishing source

Open [repository Settings → Pages](https://github.com/nielsmadan/loadout/settings/pages).
Under **Build and deployment**, open the **Source** dropdown (initially **Deploy from a branch**)
and select **GitHub Actions**.

The Docs workflow validates pull requests. A successful build on `main` uploads the Pages
artifact and deploys it through the `github-pages` environment. Manual runs are available in
[Actions](https://github.com/nielsmadan/loadout/actions/workflows/docs.yml); deployment remains
restricted to `main`.

The workflow reacts to changes in public pages/assets, overrides, `mkdocs.yml`, dependencies,
`Justfile`, and the workflow itself. It installs locked dependencies. Build and deployment
use separate jobs, with Pages write and OIDC permissions limited to deployment.

See [GitHub's custom workflow guide](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Verify ownership of loadoutai.dev

Open your **GitHub account settings → Pages**, rather than repository settings. Add
`loadoutai.dev` as a verified domain. GitHub supplies a TXT record:

| Type | DNS name | Value |
| --- | --- | --- |
| TXT | `_github-pages-challenge-nielsmadan` | The exact verification value GitHub displays |

The fully qualified name is `_github-pages-challenge-nielsmadan.loadoutai.dev`. DNS providers
differ on whether their form expects the short name or the full name.

Check it, then complete verification in GitHub:

```sh
dig _github-pages-challenge-nielsmadan.loadoutai.dev TXT +noall +answer
```

Keep the TXT record after verification.
[GitHub's domain verification instructions](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/verifying-your-custom-domain-for-github-pages).

## Bind the domain and set DNS

Set **Custom domain** to `loadoutai.dev` in the repository's Pages settings before pointing
the serving DNS records at GitHub.

At the domain's DNS provider, use these apex records:

| Type | Name | Value |
| --- | --- | --- |
| A | `@` | `185.199.108.153` |
| A | `@` | `185.199.109.153` |
| A | `@` | `185.199.110.153` |
| A | `@` | `185.199.111.153` |
| CNAME | `www` | `nielsmadan.github.io` |

For IPv6, also add:

| Type | Name | Value |
| --- | --- | --- |
| AAAA | `@` | `2606:50c0:8000::153` |
| AAAA | `@` | `2606:50c0:8001::153` |
| AAAA | `@` | `2606:50c0:8002::153` |
| AAAA | `@` | `2606:50c0:8003::153` |

Remove conflicting parking/web-forwarding records for the same names. Preserve unrelated mail
and TXT records. The `www` target is the account hostname without a repository path. With the
apex domain selected, GitHub redirects `www.loadoutai.dev` to it.

These values were checked against
[GitHub's custom-domain documentation](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site)
on 2026-09-07.

## Enable HTTPS and verify

After DNS resolves and GitHub has provisioned the certificate, enable **Enforce HTTPS** in the
repository's Pages settings. Certificate readiness can take time after DNS changes. GitHub
documents its checks in
[Securing your GitHub Pages site with HTTPS](https://docs.github.com/en/pages/getting-started-with-github-pages/securing-your-github-pages-site-with-https).

Check the records and responses:

```sh
dig loadoutai.dev A +noall +answer
dig loadoutai.dev AAAA +noall +answer
dig www.loadoutai.dev CNAME +noall +answer
curl -I https://loadoutai.dev/
curl -I https://www.loadoutai.dev/
```

The A records should match the table, the apex should serve the site successfully, and the www
response should redirect to the apex. Open the site and check navigation, search, and both themes.

## Which setting owns what

| Setting | Responsibility |
| --- | --- |
| DNS records | Route the domain to GitHub Pages |
| Repository Pages custom domain | Bind `loadoutai.dev` to this repository |
| Account verification TXT record | Verify ownership of the domain |
| `mkdocs.yml` → `site_url` | Generate canonical, sitemap, and social-image URLs |

GitHub ignores `CNAME` files for custom Actions deployments. This site uses the repository
setting for its domain binding. Changing `site_url` alone does not bind a domain.

## Maintaining the site

Update the relevant public guide when behavior changes, then run `just docs-build` and
`just check`. The README links to the public site, while the existing `docs/reference/`
research stays in the repository.

Keep `docs/assets/logo.svg` and `docs/user/assets/logo.svg` identical. The light and favicon variants
carry the same geometry with theme-appropriate colors. The social preview's editable source is
`docs/assets/social.svg`; regenerate it after changing the brand or headline:

```sh
magick -background none docs/assets/social.svg docs/user/assets/social.png
```

The PNG is committed, so building the documentation does not require ImageMagick.
The `.superpowers/` visual-comparison files are local scratch and are gitignored.

## Deployment troubleshooting

- **Build fails:** read the full strict-build output and fix its named page, link, or configuration.
- **Pages environment rejects deployment:** check that Pages uses GitHub Actions and that the
  environment permits deployment from `main`.
- **DNS check fails:** compare the apex and www records with the tables and remove conflicting
  records for those names.
- **HTTPS is not ready:** confirm the serving records and repository domain match, then wait for
  GitHub's certificate provisioning.
- **The site serves another project:** check the custom domain on this repository and the
  account-level domain verification.
