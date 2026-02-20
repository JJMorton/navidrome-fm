`navidrome-fm`: sync play counts from last.fm to a Navidrome database.

---

```
usage: navidrome-fm [-h] -u USER [--navidrome-user NAVIDROME_USER] {info,fetch,match,update-counts,update-scrobbles} ...

positional arguments:
  {info,fetch,match,update-counts,update-scrobbles}
    info                show statistics of saved scrobbles
    fetch               fetch and save scrobbles from last.fm
    match               match scrobbles with tracks in Navidrome
    update-counts       update Navidrome play counts with last.fm scrobbles
    update-scrobbles    add all last.fm scrobbles to Navidrome's native scrobbles

options:
  -h, --help            show this help message and exit
  -u USER, --user USER  last.fm username
  --navidrome-user NAVIDROME_USER
                        Navidrome username, required if multiple available
```

**Examples**

Fetch all new scrobbles from last.fm:
```
 $ navidrome-fm -u USER fetch
```

Match scrobbled tracks with those in a Navidrome database, exactly:
```
 $ navidrome-fm -u USER match --database navidrome.db
```

Match scrobbled tracks with those in a Navidrome database, with fuzzy matching:
```
 $ navidrome-fm -u USER match --database navidrome.db --fuzzy
```

Manually resolve remaining fuzzy matches below a threshold:
```
 $ navidrome-fm -u USER match --database navidrome.db --fuzzy --resolve
```

Update the play counts in the Navidrome database:
```
 $ navidrome-fm -u USER update-counts --database navidrome.db
```
