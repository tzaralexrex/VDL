# VDL
Universal video downloader

Универсальный загрузчик видео с различных сайтов.
Фактически - "обёртка" к yt-dlp, соответственно, теоретически поддерживает все сайты, которые умеет обрабатывать yt-dlp (https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).
Авторизация на сайтах - с помощью cookie из браузера. Желательно сохранить их отдельно в формате Netscape.
Требует для работы импорт yt-dlp, browser-cookie3, colorama, psutil (устанавливает автоматически), а также ffmpeg рядом или по системному пути.

