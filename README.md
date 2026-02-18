`navidrome-fm`  
Sync play counts from last.fm to a Navidrome database.

---

```
usage: navidrome-fm [-h] -u USER {info,get-scrobbles,match-scrobbles,update-counts} ...

positional arguments:
  {info,get-scrobbles,match-scrobbles,update-counts}
    info                show statistics of saved scrobbles
    get-scrobbles       fetch and save scrobbles from last.fm
    match-scrobbles     match scrobbles with tracks in Navidrome
    update-counts       update Navidrome play counts with last.fm scrobbles

options:
  -h, --help            show this help message and exit
  -u USER, --user USER  last.fm username
```

**Examples**

Fetch all new scrobbles from last.fm:
```
 $ navidrome-fm -u USER get-scrobbles
```

Match scrobbled tracks with those in a Navidrome database:
```
 $ navidrome-fm -u USER match-scrobbles --database navidrome.db
```

Manually resolve remaining fuzzy matches below a threshold:
```
 $ navidrome-fm -u USER match-scrobbles --database navidrome.db --resolve
```

Update the play counts in the Navidrome database:
```
 $ navidrome-fm -u USER update-counts --database navidrome.db
```
