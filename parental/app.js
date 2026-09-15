'use strict';
/* Parental Controls for Pi-hole — client-side, authenticates with the admin
 * password (same password as the dashboard) and drives the local /api. */
(function () {
  var DEFAULT_GROUP = 0, KIDS_GROUP = 1;
  var LOCKED_GROUP_NAME = 'Paused', LOCK_REGEX = '.+', KILL_REGEX = '.*';
  var SID_KEY = 'pihole_bahrain_sid';

  var SERVICE_META = {
    'YouTube': 'YouTube', 'TikTok': 'TikTok',
    'Instagram': 'Instagram', 'Snapchat': 'Snapchat',
    'Facebook': 'Facebook', 'X (Twitter)': 'X / Twitter',
    'Netflix': 'Netflix', 'Roblox': 'Roblox',
    'Steam': 'Steam', 'PlayStation': 'PlayStation',
    'Xbox': 'Xbox', 'WhatsApp': 'WhatsApp',
    'Telegram': 'Telegram', 'Discord': 'Discord',
    'Microsoft Teams': 'Teams', 'ChatGPT': 'ChatGPT'
  };
  var CATEGORIES = [
    ['Social', ['Instagram', 'Snapchat', 'Facebook', 'X (Twitter)', 'TikTok']],
    ['Video', ['YouTube', 'Netflix']],
    ['Games', ['Roblox', 'Steam', 'PlayStation', 'Xbox']],
    ['Messaging', ['WhatsApp', 'Telegram', 'Discord', 'Microsoft Teams']],
    ['AI', ['ChatGPT']]
  ];
  var HOMEWORK_BLOCK = ['Instagram', 'Snapchat', 'Facebook', 'X (Twitter)', 'TikTok',
    'YouTube', 'Netflix', 'Roblox', 'Steam', 'PlayStation', 'Xbox', 'Discord'];
var ICONS={
  "YouTube":["M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z","#FF0000"],
  "TikTok":["M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z","#000000"],
  "Instagram":["M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077","#E4405F"],
  "Snapchat":["M12.206.793c.99 0 4.347.276 5.93 3.821.529 1.193.403 3.219.299 4.847l-.003.06c-.012.18-.022.345-.03.51.075.045.203.09.401.09.3-.016.659-.12 1.033-.301.165-.088.344-.104.464-.104.182 0 .359.029.509.09.45.149.734.479.734.838.015.449-.39.839-1.213 1.168-.089.029-.209.075-.344.119-.45.135-1.139.36-1.333.81-.09.224-.061.524.12.868l.015.015c.06.136 1.526 3.475 4.791 4.014.255.044.435.27.42.509 0 .075-.015.149-.045.225-.24.569-1.273.988-3.146 1.271-.059.091-.12.375-.164.57-.029.179-.074.36-.134.553-.076.271-.27.405-.555.405h-.03c-.135 0-.313-.031-.538-.074-.36-.075-.765-.135-1.273-.135-.3 0-.599.015-.913.074-.6.104-1.123.464-1.723.884-.853.599-1.826 1.288-3.294 1.288-.06 0-.119-.015-.18-.015h-.149c-1.468 0-2.427-.675-3.279-1.288-.599-.42-1.107-.779-1.707-.884-.314-.045-.629-.074-.928-.074-.54 0-.958.089-1.272.149-.211.043-.391.074-.54.074-.374 0-.523-.224-.583-.42-.061-.192-.09-.389-.135-.567-.046-.181-.105-.494-.166-.57-1.918-.222-2.95-.642-3.189-1.226-.031-.063-.052-.15-.055-.225-.015-.243.165-.465.42-.509 3.264-.54 4.73-3.879 4.791-4.02l.016-.029c.18-.345.224-.645.119-.869-.195-.434-.884-.658-1.332-.809-.121-.029-.24-.074-.346-.119-1.107-.435-1.257-.93-1.197-1.273.09-.479.674-.793 1.168-.793.146 0 .27.029.383.074.42.194.789.3 1.104.3.234 0 .384-.06.465-.105l-.046-.569c-.098-1.626-.225-3.651.307-4.837C7.392 1.077 10.739.807 11.727.807l.419-.015h.06z","#FFFC00"],
  "Facebook":["M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z","#1877F2"],
  "X (Twitter)":["M14.234 10.162 22.977 0h-2.072l-7.591 8.824L7.251 0H.258l9.168 13.343L.258 24H2.33l8.016-9.318L16.749 24h6.993zm-2.837 3.299-.929-1.329L3.076 1.56h3.182l5.965 8.532.929 1.329 7.754 11.09h-3.182z","#000000"],
  "Netflix":["m5.398 0 8.348 23.602c2.346.059 4.856.398 4.856.398L10.113 0H5.398zm8.489 0v9.172l4.715 13.33V0h-4.715zM5.398 1.5V24c1.873-.225 2.81-.312 4.715-.398V14.83L5.398 1.5z","#E50914"],
  "Roblox":["M18.926 23.998 0 18.892 5.075.002 24 5.108ZM15.348 10.09l-5.282-1.453-1.414 5.273 5.282 1.453z","#000000"],
  "Steam":["M11.979 0C5.678 0 .511 4.86.022 11.037l6.432 2.658c.545-.371 1.203-.59 1.912-.59.063 0 .125.004.188.006l2.861-4.142V8.91c0-2.495 2.028-4.524 4.524-4.524 2.494 0 4.524 2.031 4.524 4.527s-2.03 4.525-4.524 4.525h-.105l-4.076 2.911c0 .052.004.105.004.159 0 1.875-1.515 3.396-3.39 3.396-1.635 0-3.016-1.173-3.331-2.727L.436 15.27C1.862 20.307 6.486 24 11.979 24c6.627 0 11.999-5.373 11.999-12S18.605 0 11.979 0zM7.54 18.21l-1.473-.61c.262.543.714.999 1.314 1.25 1.297.539 2.793-.076 3.332-1.375.263-.63.264-1.319.005-1.949s-.75-1.121-1.377-1.383c-.624-.26-1.29-.249-1.878-.03l1.523.63c.956.4 1.409 1.5 1.009 2.455-.397.957-1.497 1.41-2.454 1.012H7.54zm11.415-9.303c0-1.662-1.353-3.015-3.015-3.015-1.665 0-3.015 1.353-3.015 3.015 0 1.665 1.35 3.015 3.015 3.015 1.663 0 3.015-1.35 3.015-3.015zm-5.273-.005c0-1.252 1.013-2.266 2.265-2.266 1.249 0 2.266 1.014 2.266 2.266 0 1.251-1.017 2.265-2.266 2.265-1.253 0-2.265-1.014-2.265-2.265z","#1B2838"],
  "PlayStation":["M8.984 2.596v17.547l3.915 1.261V6.688c0-.69.304-1.151.794-.991.636.18.76.814.76 1.505v5.875c2.441 1.193 4.362-.002 4.362-3.152 0-3.237-1.126-4.675-4.438-5.827-1.307-.448-3.728-1.186-5.39-1.502zm4.656 16.241l6.296-2.275c.715-.258.826-.625.246-.818-.586-.192-1.637-.139-2.357.123l-4.205 1.5V14.98l.24-.085s1.201-.42 2.913-.615c1.696-.18 3.785.03 5.437.661 1.848.601 2.04 1.472 1.576 2.072-.465.6-1.622 1.036-1.622 1.036l-8.544 3.107V18.86zM1.807 18.6c-1.9-.545-2.214-1.668-1.352-2.32.801-.586 2.16-1.052 2.16-1.052l5.615-2.013v2.313L4.205 17c-.705.271-.825.632-.239.826.586.195 1.637.15 2.343-.12L8.247 17v2.074c-.12.03-.256.044-.39.073-1.939.331-3.996.196-6.038-.479z","#003791"],
  "Xbox":["M4.102 21.033C6.211 22.881 8.977 24 12 24c3.026 0 5.789-1.119 7.902-2.967 1.877-1.912-4.316-8.709-7.902-11.417-3.582 2.708-9.779 9.505-7.898 11.417zm11.16-14.406c2.5 2.961 7.484 10.313 6.076 12.912C23.002 17.48 24 14.861 24 12.004c0-3.34-1.365-6.362-3.57-8.536 0 0-.027-.022-.082-.042-.063-.022-.152-.045-.281-.045-.592 0-1.985.434-4.805 3.246zM3.654 3.426c-.057.02-.082.041-.086.042C1.365 5.642 0 8.664 0 12.004c0 2.854.998 5.473 2.661 7.533-1.401-2.605 3.579-9.951 6.08-12.91-2.82-2.813-4.216-3.245-4.806-3.245-.131 0-.223.021-.281.046v-.002zM12 3.551S9.055 1.828 6.755 1.746c-.903-.033-1.454.295-1.521.339C7.379.646 9.659 0 11.984 0H12c2.334 0 4.605.646 6.766 2.085-.068-.046-.615-.372-1.52-.339C14.946 1.828 12 3.545 12 3.545v.006z","#107C10"],
  "WhatsApp":["M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z","#25D366"],
  "Telegram":["M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z","#26A5E4"],
  "Discord":["M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z","#5865F2"],
  "Microsoft Teams":["M20.625 8.127q-.55 0-1.025-.205-.475-.205-.832-.563-.358-.357-.563-.832Q18 6.053 18 5.502q0-.54.205-1.02t.563-.837q.357-.358.832-.563.474-.205 1.025-.205.54 0 1.02.205t.837.563q.358.357.563.837.205.48.205 1.02 0 .55-.205 1.025-.205.475-.563.832-.357.358-.837.563-.48.205-1.02.205zm0-3.75q-.469 0-.797.328-.328.328-.328.797 0 .469.328.797.328.328.797.328.469 0 .797-.328.328-.328.328-.797 0-.469-.328-.797-.328-.328-.797-.328zM24 10.002v5.578q0 .774-.293 1.46-.293.685-.803 1.194-.51.51-1.195.803-.686.293-1.459.293-.445 0-.908-.105-.463-.106-.85-.329-.293.95-.855 1.729-.563.78-1.319 1.336-.756.557-1.67.861-.914.305-1.898.305-1.148 0-2.162-.398-1.014-.399-1.805-1.102-.79-.703-1.312-1.664t-.674-2.086h-5.8q-.411 0-.704-.293T0 16.881V6.873q0-.41.293-.703t.703-.293h8.59q-.34-.715-.34-1.5 0-.727.275-1.365.276-.639.75-1.114.475-.474 1.114-.75.638-.275 1.365-.275t1.365.275q.639.276 1.114.75.474.475.75 1.114.275.638.275 1.365t-.275 1.365q-.276.639-.75 1.113-.475.475-1.114.75-.638.276-1.365.276-.188 0-.375-.024-.188-.023-.375-.058v1.078h10.875q.469 0 .797.328.328.328.328.797zM12.75 2.373q-.41 0-.78.158-.368.158-.638.434-.27.275-.428.639-.158.363-.158.773 0 .41.158.78.159.368.428.638.27.27.639.428.369.158.779.158.41 0 .773-.158.364-.159.64-.428.274-.27.433-.639.158-.369.158-.779 0-.41-.158-.773-.159-.364-.434-.64-.275-.275-.639-.433-.363-.158-.773-.158zM6.937 9.814h2.25V7.94H2.814v1.875h2.25v6h1.875zm10.313 7.313v-6.75H12v6.504q0 .41-.293.703t-.703.293H8.309q.152.809.556 1.5.405.691.985 1.19.58.497 1.318.779.738.281 1.582.281.926 0 1.746-.352.82-.351 1.436-.966.615-.616.966-1.43.352-.815.352-1.752zm5.25-1.547v-5.203h-3.75v6.855q.305.305.691.452.387.146.809.146.469 0 .879-.176.41-.175.715-.48.304-.305.48-.715t.176-.879Z","#6264A7"],
  "ChatGPT":["M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z","#10A37F"],
  "_default":["M12 2a10 10 0 100 20 10 10 0 000-20zm0 3a7 7 0 110 14 7 7 0 010-14z","#6b7280"]
};

  function svgIcon(name) {
    var ic = ICONS[name] || ICONS._default;
    return '<span class="ic" style="background:' + ic[1] + '1a;color:' + ic[1] + '"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="' + ic[0] + '"/></svg></span>';
  }

  var SID = sessionStorage.getItem(SID_KEY) || '';
  var S = null, BUSY = false;

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function toast(m) { var t = $('toast'); t.textContent = m; t.classList.add('show');
    clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove('show'); }, 2800); }
  function enc(s) { return encodeURIComponent(s); }

  function headers(body) {
    var h = {};
    if (body !== undefined) h['Content-Type'] = 'application/json';
    if (SID) h['sid'] = SID;
    return h;
  }
  function errmsg(j) {
    if (!j) return 'unknown error';
    if (j.error) { var e = j.error; return typeof e === 'string' ? e : (e.message || e.hint || JSON.stringify(e)); }
    return JSON.stringify(j).slice(0, 120);
  }
  function rawFetch(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { r: r, j: j }; });
    });
  }
  function api(method, path, body) {
    return rawFetch(path, { method: method, headers: headers(body), body: body !== undefined ? JSON.stringify(body) : undefined })
      .then(function (x) {
        if (x.r.status === 401 || x.r.status === 403) { SID = ''; sessionStorage.removeItem(SID_KEY); showLogin(); throw new Error('Please sign in'); }
        if (!x.r.ok) throw new Error(errmsg(x.j));
        return x.j;
      });
  }
  function tryLogin(pw) {
    return rawFetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
      .then(function (x) {
        if (x.r.ok && x.j.session && x.j.session.sid) { SID = x.j.session.sid; sessionStorage.setItem(SID_KEY, SID); return true; }
        return false;
      });
  }
  function showLogin() { $('login').classList.remove('hidden'); $('app').classList.add('hidden'); }
  function showApp() { $('login').classList.add('hidden'); $('app').classList.remove('hidden'); }

  function restart() {
    return api('POST', '/api/action/restartdns').catch(function () { /* FTL may drop mid-restart */ })
      .then(function () {
        var i = 0;
        function wait() {
          return new Promise(function (res) { setTimeout(res, 600); }).then(function () {
            return rawFetch('/api/info/version', { headers: headers() }).then(function (x) {
              if (x.r.ok) return true; if (++i < 50) return wait(); return true;
            }).catch(function () { if (++i < 50) return wait(); return true; });
          });
        }
        return wait();
      });
  }

  // ---- state ----
  function loadState() {
    return Promise.all([
      api('GET', '/api/lists'), api('GET', '/api/clients'), api('GET', '/api/groups'),
      api('GET', '/api/domains?length=10000'),
      api('GET', '/api/info/version').catch(function () { return {}; })
    ]).then(function (r) {
      var lists = r[0], clients = r[1], groups = r[2], domains = r[3], version = r[4];
      var svcs = [];
      (lists.lists || []).forEach(function (l) {
        if ((l.groups || []).indexOf(KIDS_GROUP) >= 0) {
          svcs.push({ comment: l.comment || l.address, address: l.address, enabled: !!l.enabled, number: l.number || 0 });
        }
      });
      var order = Object.keys(SERVICE_META);
      svcs.sort(function (a, b) {
        var oa = order.indexOf(a.comment), ob = order.indexOf(b.comment);
        return (oa < 0 ? 999 : oa) - (ob < 0 ? 999 : ob);
      });
      var lockedGid = null;
      (groups.groups || []).forEach(function (g) { if (g.name === LOCKED_GROUP_NAME) lockedGid = g.id; });
      var kids = [];
      (clients.clients || []).forEach(function (c) {
        if ((c.groups || []).indexOf(KIDS_GROUP) >= 0) {
          kids.push({ client: c.client, comment: c.comment || '', locked: lockedGid != null && (c.groups || []).indexOf(lockedGid) >= 0 });
        }
      });
      var kill = { exists: false, enabled: false, groups: [DEFAULT_GROUP] };
      (domains.domains || []).forEach(function (d) {
        if (d.type === 'deny' && d.kind === 'regex' && d.domain === KILL_REGEX) {
          kill = { exists: true, enabled: !!d.enabled, groups: d.groups || [DEFAULT_GROUP] };
        }
      });
      var core = ((version.version || {}).core || {}).local || {};
      S = { services: svcs, kids: kids, kill: kill, cats: CATEGORIES, meta: SERVICE_META, version: core.version || '' };
      render();
    });
  }

  // ---- mutations ----
  function setList(comment, enabled) {
    var s = null;
    S.services.forEach(function (x) { if (x.comment === comment) s = x; });
    if (!s) return Promise.reject(new Error('service not found: ' + comment));
    return api('PUT', '/api/lists/' + enc(s.address) + '?type=block',
      { address: s.address, type: 'block', comment: comment, groups: [KIDS_GROUP], enabled: !!enabled });
  }
  function setKill(enabled) {
    if (S.kill.exists) {
      return api('PUT', '/api/domains/deny/regex/' + enc(KILL_REGEX) + '?type=deny&kind=regex',
        { domain: KILL_REGEX, type: 'deny', kind: 'regex', groups: S.kill.groups, enabled: !!enabled });
    }
    return api('POST', '/api/domains/deny/regex',
      { domain: KILL_REGEX, kind: 'regex', groups: [DEFAULT_GROUP], enabled: !!enabled });
  }
  function apply(changes, killVal) {
    var p = Promise.resolve();
    changes.forEach(function (c) { p = p.then(function () { return setList(c[0], c[1]); }); });
    if (killVal !== undefined) p = p.then(function () { return setKill(killVal); });
    return p.then(function () { return restart(); });
  }
  function ensureLockGroup() {
    return api('GET', '/api/groups').then(function (g) {
      var found = null; (g.groups || []).forEach(function (x) { if (x.name === LOCKED_GROUP_NAME) found = x; });
      if (found) return found.id;
      return api('POST', '/api/groups', { name: LOCKED_GROUP_NAME, comment: 'Per-device pause', enabled: true })
        .then(function (j) {
          var nf = null; (j.groups || []).forEach(function (x) { if (x.name === LOCKED_GROUP_NAME) nf = x; });
          return nf ? nf.id : api('GET', '/api/groups').then(function (g2) {
            var f2 = null; (g2.groups || []).forEach(function (x) { if (x.name === LOCKED_GROUP_NAME) f2 = x; });
            return f2.id;
          });
        });
    }).then(function (gid) {
      return api('GET', '/api/domains?length=10000').then(function (d) {
        var lock = null; (d.domains || []).forEach(function (x) { if (x.kind === 'regex' && x.domain === LOCK_REGEX) lock = x; });
        if (!lock) return api('POST', '/api/domains/deny/regex', { domain: LOCK_REGEX, kind: 'regex', groups: [gid], enabled: true });
        return api('PUT', '/api/domains/deny/regex/' + enc(LOCK_REGEX) + '?type=deny&kind=regex',
          { domain: LOCK_REGEX, type: 'deny', kind: 'regex', groups: [gid], enabled: true });
      }).then(function () { return gid; });
    });
  }
  function setLock(client, locked) {
    return api('GET', '/api/clients').then(function (d) {
      var row = null; (d.clients || []).forEach(function (c) { if (c.client === client) row = c; });
      if (!row) throw new Error('device not found');
      var groups = (row.groups || []).slice();
      var p = Promise.resolve();
      if (locked) p = ensureLockGroup().then(function (gid) { if (groups.indexOf(gid) < 0) groups.push(gid); });
      else {
        p = api('GET', '/api/groups').then(function (g) {
          var gid = null; (g.groups || []).forEach(function (x) { if (x.name === LOCKED_GROUP_NAME) gid = x.id; });
          if (gid != null) groups = groups.filter(function (x) { return x !== gid; });
        });
      }
      return p.then(function () {
        if (!groups.length) groups = [DEFAULT_GROUP];
        return api('PUT', '/api/clients/' + enc(client), { client: client, comment: row.comment || '', groups: groups });
      }).then(function () { return restart(); });
    });
  }
  function addKid(client, comment) {
    client = (client || '').trim();
    if (!/^[0-9a-fA-F:.]+$/.test(client)) throw new Error('not a valid IP address');
    return api('GET', '/api/clients').then(function (d) {
      var row = null; (d.clients || []).forEach(function (c) { if (c.client === client) row = c; });
      var groups = [DEFAULT_GROUP, KIDS_GROUP].concat(row ? (row.groups || []) : []);
      groups = groups.filter(function (v, i, a) { return a.indexOf(v) === i; });
      var body = { client: client, comment: (comment || '').trim() || (row && row.comment) || 'Kid device', groups: groups };
      if (row) return api('PUT', '/api/clients/' + enc(client), body);
      return api('POST', '/api/clients', body);
    }).then(function () { return restart(); });
  }
  function removeKid(client) {
    return api('GET', '/api/clients').then(function (d) {
      var row = null; (d.clients || []).forEach(function (c) { if (c.client === client) row = c; });
      if (!row) throw new Error('device not found');
      var groups = (row.groups || []).filter(function (g) { return g !== KIDS_GROUP; });
      if (!groups.length) groups = [DEFAULT_GROUP];
      return api('PUT', '/api/clients/' + enc(client), { client: client, comment: row.comment || '', groups: groups });
    }).then(function () { return restart(); });
  }
  function preset(name) {
    if (name === 'homework') {
      var ch = Object.keys(SERVICE_META).map(function (c) { return [c, HOMEWORK_BLOCK.indexOf(c) >= 0]; });
      return apply(ch, false);
    }
    if (name === 'free') return apply(Object.keys(SERVICE_META).map(function (c) { return [c, false]; }), false);
    if (name === 'bedtime') return setKill(true).then(function () { return restart(); });
    return Promise.reject(new Error('unknown preset'));
  }
  function freeTime() { return apply(Object.keys(SERVICE_META).map(function (c) { return [c, false]; }), false); }
  function blockAll() { return apply(Object.keys(SERVICE_META).map(function (c) { return [c, true]; }), false); }

  // ---- render ----
  function render() {
    var k = $('kids');
    k.innerHTML = S.kids.length ? S.kids.map(function (d) {
      var nm = esc(d.comment || d.client);
      return '<div class="kid"><div><div class="name">' + nm + (d.locked ? ' <span class="tag paused">paused</span>' : '') +
        '</div><div class="meta">' + esc(d.client) + '</div></div><div style="display:flex;gap:8px">' +
        '<button data-act="lock" data-client="' + esc(d.client) + '" data-want="' + (!d.locked) + '">' + (d.locked ? 'Resume' : 'Pause') + '</button>' +
        '<button class="danger" data-act="rm" data-client="' + esc(d.client) + '">Remove</button></div></div>';
    }).join('') : '<div style="color:var(--dim);padding:4px 0">No devices yet &mdash; nothing is filtered.</div>';

    var by = {}; S.services.forEach(function (s) { by[s.comment] = s; });
    var html = '';
    S.cats.forEach(function (cat) {
      var names = cat[1].filter(function (n) { return by[n]; });
      if (!names.length) return;
      html += '<div class="sec">' + esc(cat[0]) + '</div><div class="grid">';
      names.forEach(function (n) {
        var s = by[n];
        html += '<div class="svc">' + svgIcon(n) + '<span class="nm">' + esc(S.meta[n] || n) + '</span>' +
          '<button class="sw ' + (s.enabled ? 'on' : '') + '" title="' + (s.enabled ? 'Blocked' : 'Allowed') + '" ' +
          'data-act="toggle" data-comment="' + esc(s.comment) + '" data-want="' + (!s.enabled) + '"></button></div>';
      });
      html += '</div>';
    });
    $('svcs').innerHTML = html;
    var blocked = S.services.filter(function (s) { return s.enabled; }).length;
    $('svcCount').textContent = blocked + ' of ' + S.services.length + ' blocked';

    var on = S.kill.enabled;
    $('bigwrap').className = 'big' + (on ? ' on' : '');
    $('killtitle').textContent = on ? 'Internet is blocked' : 'Block the entire internet';
    $('kill').className = 'bigbtn' + (on ? ' armed' : '');
    $('kill').textContent = on ? 'Unblock all' : 'Block all';

    var paused = S.kids.filter(function (d) { return d.locked; }).length;
    $('status').textContent = on ? 'Internet BLOCKED' : (paused ? paused + ' device paused' : 'Internet allowed');
    $('foot').textContent = 'Pi-hole ' + (S.version || '') + ' \u00B7 ' + S.kids.length + ' kid device(s) \u00B7 ' + blocked + '/' + S.services.length + ' services blocked';

    loadDevices();
  }
  function loadDevices() {
    return api('GET', '/api/network/devices?max_devices=500').then(function (d) {
      var sel = $('devpick'), known = {};
      S.kids.forEach(function (k) { known[k.client] = 1; });
      var o = [];
      (d.devices || []).forEach(function (dev) {
        (dev.ips || []).forEach(function (i) {
          if (!i.ip) return;
          o.push('<option value="' + esc(i.ip) + '">' + esc((i.name || dev.macVendor || i.ip) + ' \u00B7 ' + i.ip) + (known[i.ip] ? ' (kid)' : '') + '</option>');
        });
      });
      sel.innerHTML = o.length ? '<option value="">\u2014 pick a device seen on your network \u2014</option>' + o.join('')
        : '<option value="">No devices seen yet \u2014 type the IP below</option>';
    }).catch(function () {});
  }
  function pick(ip) { if (ip) $('devip').value = ip; }

  function withBusy(fn, okmsg) {
    if (BUSY) return; BUSY = true;
    document.querySelectorAll('.sw,.preset,.bigbtn,button').forEach(function (b) { b.classList.add('busy'); });
    fn().then(function () { toast(okmsg); }).catch(function (e) { toast('Failed: ' + e.message); })
      .then(function () { BUSY = false; document.querySelectorAll('.busy').forEach(function (b) { b.classList.remove('busy'); }); return loadState(); });
  }

  var TIMER_KEY = 'pihole_bahrain_timer';
  var timerTick = null;
  function fmtDur(sec) {
    sec = Math.max(0, Math.floor(sec));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var p = function (n) { return String(n).padStart(2, '0'); };
    return h > 0 ? (h + ':' + p(m) + ':' + p(s)) : (p(m) + ':' + p(s));
  }
  function timerExpires() {
    try { var t = JSON.parse(localStorage.getItem(TIMER_KEY) || 'null'); return (t && t.expires) ? t.expires : null; }
    catch (e) { return null; }
  }
  function renderTimer(leftMs) {
    var cancel = $('timerCancel'), status = $('timerStatus'), count = $('timerCount');
    if (!cancel || !status) return;
    if (leftMs === null || leftMs === undefined) {
      cancel.style.display = 'none';
      if (count) count.style.display = 'none';
      status.textContent = 'Unblocks everything, then re-blocks when the timer ends.';
    } else {
      cancel.style.display = '';
      if (count) { count.style.display = ''; count.textContent = fmtDur(leftMs / 1000); }
      status.textContent = 'Free time active — re-blocks when the timer ends.';
    }
  }
  function scheduleTimer() {
    clearInterval(timerTick);
    var exp = timerExpires();
    if (!exp) { renderTimer(null); return; }
    function upd() {
      var left = exp - Date.now();
      if (left <= 0) {
        clearInterval(timerTick);
        localStorage.removeItem(TIMER_KEY);
        blockAll().then(function () { return loadState(); }).catch(function () {});
        return;
      }
      renderTimer(left);
    }
    upd();
    timerTick = setInterval(upd, 1000);
  }

  // ---- wiring ----
  window.PC = {
    toggle: function (n, w) { withBusy(function () { return setList(n, w); }, (w ? 'Blocked ' : 'Allowed ') + n); },
    lock: function (c, l) { withBusy(function () { return setLock(c, l); }, (l ? 'Paused ' : 'Resumed ') + c); },
    rm: function (c) { if (!confirm('Stop kid filtering for ' + c + '?')) return; withBusy(function () { return removeKid(c); }, 'Removed'); },
    add: function () {
      var c = $('devip').value.trim(), n = $('devname').value.trim();
      if (!c) { toast('Enter the device IP'); return; }
      withBusy(function () { return addKid(c, n); }, 'Device added').then(function () { $('devip').value = ''; $('devname').value = ''; });
    },
    preset: function (n) { withBusy(function () { return preset(n); }, 'Applied: ' + n); },
    kill: function () {
      var want = !S.kill.enabled;
      if (want && !confirm('Block the internet for EVERYONE using this Pi-hole?')) return;
      withBusy(function () { return setKill(want).then(function () { return restart(); }); }, want ? 'Internet blocked' : 'Internet unblocked');
    },
    timer: function (h) {
      withBusy(function () {
        return freeTime().then(function () {
          localStorage.setItem(TIMER_KEY, JSON.stringify({ expires: Date.now() + h * 3600000 }));
          scheduleTimer();
        });
      }, 'Free time for ' + h + ' hour' + (h === 1 ? '' : 's'));
    },
    timerCustom: function () {
      var h = parseFloat($('timerHours').value);
      if (!(h > 0)) { toast('Enter the number of hours'); return; }
      window.PC.timer(h);
    },
    timerCancel: function () {
      withBusy(function () {
        return blockAll().then(function () {
          localStorage.removeItem(TIMER_KEY);
          scheduleTimer();
        });
      }, 'Timer cancelled — blocked again');
    }
  };

  document.querySelectorAll('.preset').forEach(function (b) {
    b.addEventListener('click', function () { window.PC.preset(b.getAttribute('data-p')); });
  });
  $('addBtn').addEventListener('click', function () { window.PC.add(); });
  $('kill').addEventListener('click', function () { window.PC.kill(); });
  $('devip').addEventListener('keydown', function (e) { if (e.key === 'Enter') window.PC.add(); });

  // CSP-safe event delegation (script-src 'self' blocks inline onclick)
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('[data-act]') : null;
    if (!el) return;
    var act = el.getAttribute('data-act');
    if (act === 'toggle') window.PC.toggle(el.getAttribute('data-comment'), el.getAttribute('data-want') === 'true');
    else if (act === 'lock') window.PC.lock(el.getAttribute('data-client'), el.getAttribute('data-want') === 'true');
    else if (act === 'rm') window.PC.rm(el.getAttribute('data-client'));
    else if (act === 'timer-set') window.PC.timer(parseFloat(el.getAttribute('data-hours')));
    else if (act === 'timer-custom') window.PC.timerCustom();
    else if (act === 'timer-cancel') window.PC.timerCancel();
  });

  $('loginBtn').addEventListener('click', function () { doLogin(); });
  $('pw').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
  function doLogin() {
    var pw = $('pw').value;
    $('loginErr').textContent = '';
    $('loginBtn').disabled = true;
    tryLogin(pw).then(function (ok) {
      if (ok) { $('pw').value = ''; showApp(); scheduleTimer(); return loadState(); }
      $('loginErr').textContent = 'Wrong password.';
    }).catch(function (e) { $('loginErr').textContent = 'Error: ' + e.message; })
      .then(function () { $('loginBtn').disabled = false; });
  }

  // boot
  if (SID) {
    api('GET', '/api/info/version').then(function () { showApp(); scheduleTimer(); return loadState(); })
      .catch(function () { showLogin(); });
  } else {
    showLogin();
  }
})();
