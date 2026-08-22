# ARR application integration

> **Current boundary:** Hoardarr discovers supported Servarr state, builds an
> exact product-aware change preview, then applies and reads back root folders,
> remote-path mappings and supported download-client fields. Writes are
> idempotent and audited, redact API keys, report partial failure honestly and
> run compensating actions where the remote API permits them. Prowlarr remains
> discovery-only because it does not own the storage-root operations described
> here; Readarr and Whisparr require their documented opt-in support levels.

The storage wizard ends with an optional **Connect your apps** step. Hoardarr
creates a predictable folder layout, verifies permissions and path visibility,
then reconciles supported applications through their APIs. The step always
shows a change preview and remains skippable.

## Default folder layout

The simple-mode default is:

```text
/data/
  work/
    torrents/
    usenet/
  downloads/
    torrents/
      movies/
      tv/
      music/
      books/
    usenet/
      movies/
      tv/
      music/
      books/
  media/
    movies/
    tv/
    music/
    books/
```

`/data/work` is the optional SSD-backed incomplete/unpack workspace.
`/data/downloads` and `/data/media` are HDD-backed. Completed downloads move to
the HDD tier before an ARR application imports them, allowing hardlinks when
the completed and library paths resolve to the same underlying filesystem.
Hoardarr validates that fact instead of assuming it from similar-looking paths.

The logical paths stay stable if disks, pools, exports, or the storage host
change. Advanced mode may rename or add categories, but it uses the same path
and hardlink checks.

The initial simple-mode mapping is explicit:

| Application role | Library root | Torrent category/path | Usenet category/path |
| --- | --- | --- | --- |
| Sonarr | `/data/media/tv` | `tv` -> `/data/downloads/torrents/tv` | `tv` -> `/data/downloads/usenet/tv` |
| Radarr | `/data/media/movies` | `movies` -> `/data/downloads/torrents/movies` | `movies` -> `/data/downloads/usenet/movies` |
| Lidarr | `/data/media/music` | `music` -> `/data/downloads/torrents/music` | `music` -> `/data/downloads/usenet/music` |
| Readarr | `/data/media/books` | `books` -> `/data/downloads/torrents/books` | `books` -> `/data/downloads/usenet/books` |

Whisparr and any custom library type require the user to confirm the category
and library name instead of silently assigning a potentially sensitive default.
Prowlarr, Bazarr, request managers, and media servers do not all own storage
roots; their adapters expose only the capabilities their APIs actually provide.
The wizard never invents a root-folder operation for an application that does
not support one.

## Permissions

The wizard proposes a shared service group, setgid directories, a cooperative
umask, and default ACLs. It displays all users and services that will gain
access. It never uses world-writable permissions as a shortcut.

Existing content is treated as imported data. The simple wizard creates only
missing directories and applies inherited permissions to directories it creates;
it never runs an implicit recursive `chown` or `chmod` across an existing media
tree. A requested recursive repair is an Advanced operation with an item count,
sample of affected paths, dry run, and separate confirmation.

For applications on another machine, the wizard distinguishes the path exported
by Hoardarr, the path mounted on the application host, and the path reported by
the download client. It creates an ARR remote-path mapping only when those
names differ. A write/read probe and the application's own root-folder response
must prove visibility before configuration is considered healthy.

## Supported adapter roles

Adapters are capability-based rather than assuming every ARR application has
the same API:

- library-root consumers such as Sonarr, Radarr, Lidarr, Readarr, and Whisparr;
- indexer/application coordination such as Prowlarr;
- download clients such as qBittorrent, Transmission, Deluge, SABnzbd, and
  NZBGet;
- optional adjacent applications through the add-on interface.

The first release can support a smaller tested matrix, but the wizard and API
model must not hard-code one application or API version. Each adapter declares
product identity, supported version range, discovery calls, readable state,
planned changes, write calls, secret fields, and validation calls.

For a download client, the plan may set its incomplete path to `/data/work`,
completed/category paths to `/data/downloads`, and safe move-on-completion
behavior. For an ARR application, it may add the matching `/data/media` root,
configure the download client/category, and add a remote-path mapping. Prowlarr
connections are a separate capability and are not confused with storage roots.
Adding a root folder does not implicitly start a library import, rename existing
media, or change an application's existing series/artist/movie assignments.

## Wizard flow

1. Choose the standard layout or customize it in Advanced.
2. Select the service group, users, and applications that need access.
3. Add each application's base URL and API credential, or choose an existing
   saved connection.
4. Discover product and version, read current configuration, and test TLS and
   authentication.
5. Map Hoardarr storage, exported paths, application-host paths, and download-
   client paths.
6. Run capacity, write/read, hardlink, ownership, and client-state checks.
7. Review one combined plan for folders, ACLs, shares, root folders, categories,
   clients, and remote-path mappings.
8. Apply and validate. Failed external changes remain visible as retryable jobs
   with a precise recovery action.

The apply is ordered in two phases. Hoardarr first creates directories, applies
the reviewed ownership/ACL plan, exports or mounts the paths, and proves access.
Only then may application adapters create or update remote objects. The final
validation reads every object back through the same API and compares normalized
state with the approved plan.

The user can go Back, Cancel, Save for later, or open Advanced from every page.
No API mutation occurs during discovery or preview.

## Reconciliation and safety

Configuration is declarative and repeatable. Hoardarr reads current state,
normalizes paths, and changes only fields that differ. It records remote object
IDs after creation and rediscovers by stable identity when an ID changes.
Rerunning the wizard must not create duplicate root folders, download clients,
categories, or path mappings.

Removing an application from a Hoardarr plan stops management by default; it
does not delete the application's existing configuration. Deletion is a
separate explicitly confirmed action. Existing configuration is captured before
a write so Hoardarr can offer a compensating rollback when the remote API
supports it. Directory rollback removes only empty directories created by the
failed operation and never removes user data.

API keys and passwords are write-only UI fields, encrypted at rest, excluded
from logs and reports, and never returned by Hoardarr's public API. TLS
verification is the default; accepting a private CA and temporarily allowing an
unverified endpoint are distinct choices with clear warnings.

## Completion criteria

The final summary reports independently whether:

- folders and ACLs match the plan;
- every application can see its configured paths;
- the download client can transition a harmless test job or fixture from work
  to completed storage;
- hardlinks are actually possible where promised;
- root folders, categories, and remote-path mappings match without duplicates;
- no credential appears in logs or the structured operation report.

Hoardarr reports partial success honestly. A working folder layout is not rolled
back merely because one external application is offline; that application stays
in a retryable **Needs attention** state.
