`navidrome-fm`: sync play counts from last.fm to a Navidrome database.

---

```
usage: navidrome-fm [-h] -u USER {info,fetch,match,update-counts} ...

positional arguments:
  {info,fetch,match,update-counts}
    info                show statistics of saved scrobbles
    fetch               fetch and save scrobbles from last.fm
    match               match scrobbles with tracks in Navidrome
    update-counts       update Navidrome play counts with last.fm scrobbles

options:
  -h, --help            show this help message and exit
  -u USER, --user USER  last.fm username
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
