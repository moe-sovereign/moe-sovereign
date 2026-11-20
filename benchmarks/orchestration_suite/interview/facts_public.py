"""A bank of public facts for the planner-interview pilot: things a large model knows and the system's own knowledge never holds.

Each entry: (key, question en, question de, accepted answers). Only well-established facts; the answers are short strings matched by
case-insensitive containment. The bank is split by key hash into train and test, so the test facts and entities are never seen in training.
"""

CAPITALS = [
    ("Australia", "Canberra"), ("Canada", "Ottawa"), ("Brazil", "Brasília|Brasilia"), ("Turkey", "Ankara"), ("Switzerland", "Bern"),
    ("Germany", "Berlin"), ("France", "Paris"), ("Italy", "Rome|Roma"), ("Spain", "Madrid"), ("Portugal", "Lisbon|Lissabon"),
    ("Poland", "Warsaw|Warschau"), ("Austria", "Vienna|Wien"), ("Norway", "Oslo"), ("Sweden", "Stockholm"), ("Finland", "Helsinki"),
    ("Denmark", "Copenhagen|Kopenhagen"), ("Ireland", "Dublin"), ("Greece", "Athens|Athen"), ("Hungary", "Budapest"),
    ("Czechia", "Prague|Prag"), ("Netherlands", "Amsterdam"), ("Belgium", "Brussels|Brüssel"), ("Japan", "Tokyo"),
    ("China", "Beijing|Peking"), ("India", "New Delhi"), ("Indonesia", "Jakarta"), ("Thailand", "Bangkok"), ("Vietnam", "Hanoi"),
    ("South Korea", "Seoul"), ("Egypt", "Cairo|Kairo"), ("Kenya", "Nairobi"), ("Nigeria", "Abuja"), ("Ethiopia", "Addis Ababa"),
    ("Morocco", "Rabat"), ("Argentina", "Buenos Aires"), ("Chile", "Santiago"), ("Peru", "Lima"), ("Colombia", "Bogotá|Bogota"),
    ("Mexico", "Mexico City|Mexiko-Stadt"), ("Cuba", "Havana|Havanna"), ("New Zealand", "Wellington"), ("Iceland", "Reykjavik|Reykjavík"),
    ("Romania", "Bucharest|Bukarest"), ("Bulgaria", "Sofia"), ("Croatia", "Zagreb"), ("Serbia", "Belgrade|Belgrad"),
    ("Ukraine", "Kyiv|Kiev"), ("Pakistan", "Islamabad"), ("Iran", "Tehran|Teheran"), ("Iraq", "Baghdad|Bagdad"),
    ("Saudi Arabia", "Riyadh|Riad"), ("Israel", "Jerusalem"), ("Jordan", "Amman"), ("Lebanon", "Beirut"), ("Ghana", "Accra"),
    ("Senegal", "Dakar"), ("Tanzania", "Dodoma"), ("Uganda", "Kampala"), ("Algeria", "Algiers|Algier"), ("Tunisia", "Tunis"),
    ("Venezuela", "Caracas"), ("Ecuador", "Quito"), ("Bolivia", "Sucre|La Paz"), ("Uruguay", "Montevideo"), ("Paraguay", "Asunción|Asuncion"),
    ("Mongolia", "Ulaanbaatar|Ulan Bator"), ("Nepal", "Kathmandu|Kathmandu"), ("Sri Lanka", "Colombo|Sri Jayawardenepura"),
    ("Philippines", "Manila"), ("Malaysia", "Kuala Lumpur"), ("Singapore", "Singapore|Singapur"), ("Estonia", "Tallinn"),
    ("Latvia", "Riga"), ("Lithuania", "Vilnius"), ("Slovakia", "Bratislava"), ("Slovenia", "Ljubljana"), ("Luxembourg", "Luxembourg|Luxemburg"),
]

ELEMENTS = [
    ("gold", "Au"), ("silver", "Ag"), ("iron", "Fe"), ("copper", "Cu"), ("lead", "Pb"), ("tin", "Sn"), ("sodium", "Na"), ("potassium", "K"),
    ("mercury", "Hg"), ("tungsten", "W"), ("helium", "He"), ("neon", "Ne"), ("argon", "Ar"), ("carbon", "C"), ("nitrogen", "N"),
    ("oxygen", "O"), ("hydrogen", "H"), ("calcium", "Ca"), ("magnesium", "Mg"), ("zinc", "Zn"), ("nickel", "Ni"), ("platinum", "Pt"),
    ("uranium", "U"), ("chlorine", "Cl"), ("sulfur", "S"), ("phosphorus", "P"), ("aluminium", "Al"), ("titanium", "Ti"), ("lithium", "Li"),
    ("boron", "B"),
]

ATOMIC_NUMBERS = [
    ("hydrogen", "1"), ("helium", "2"), ("carbon", "6"), ("nitrogen", "7"), ("oxygen", "8"), ("sodium", "11"), ("aluminium", "13"),
    ("silicon", "14"), ("iron", "26"), ("copper", "29"), ("silver", "47"), ("gold", "79"), ("lead", "82"), ("uranium", "92"),
    ("neon", "10"), ("calcium", "20"), ("zinc", "30"), ("tin", "50"), ("mercury", "80"), ("platinum", "78"),
]

AUTHORS = [
    ("Faust", "Goethe"), ("Hamlet", "Shakespeare"), ("Don Quixote", "Cervantes"), ("The Trial", "Kafka"), ("Crime and Punishment", "Dostoevsky|Dostojewski"),
    ("War and Peace", "Tolstoy|Tolstoi"), ("Pride and Prejudice", "Austen"), ("Moby-Dick", "Melville"), ("The Odyssey", "Homer"),
    ("The Divine Comedy", "Dante"), ("Les Misérables", "Hugo"), ("The Metamorphosis", "Kafka"), ("Nineteen Eighty-Four", "Orwell"),
    ("The Great Gatsby", "Fitzgerald"), ("Ulysses", "Joyce"), ("The Magic Mountain", "Mann"), ("Steppenwolf", "Hesse"),
    ("The Tin Drum", "Grass"), ("Wuthering Heights", "Brontë|Bronte"), ("Jane Eyre", "Brontë|Bronte"), ("Oliver Twist", "Dickens"),
    ("Madame Bovary", "Flaubert"), ("The Stranger", "Camus"), ("Brave New World", "Huxley"), ("Frankenstein", "Shelley"),
    ("Dracula", "Stoker"), ("The Hobbit", "Tolkien"), ("Lolita", "Nabokov"), ("The Name of the Rose", "Eco"), ("Siddhartha", "Hesse"),
]

INVENTIONS = [
    ("the telephone", "Bell"), ("the light bulb", "Edison"), ("the theory of general relativity", "Einstein"), ("penicillin", "Fleming"),
    ("the printing press with movable type", "Gutenberg"), ("the World Wide Web", "Berners-Lee"), ("dynamite", "Nobel"),
    ("the periodic table", "Mendeleev|Mendelejew"), ("the steam engine improvements of 1769", "Watt"), ("the diesel engine", "Diesel"),
    ("the telescope observations of Jupiter's moons", "Galileo|Galilei"), ("the laws of planetary motion", "Kepler"),
    ("radioactivity", "Becquerel|Curie"), ("the polio vaccine", "Salk"), ("the first practical computer program", "Lovelace"),
    ("the structure of DNA", "Watson|Crick"), ("the law of universal gravitation", "Newton"), ("the electric battery", "Volta"),
    ("the dynamo principle of electromagnetic induction", "Faraday"), ("the Python programming language", "van Rossum"),
    ("the C programming language", "Ritchie"), ("the Linux kernel", "Torvalds"), ("the Pascal programming language", "Wirth"),
    ("the Java programming language", "Gosling"), ("the first mechanical calculator in 1642", "Pascal"),
]

EVENTS = [
    ("the fall of the Berlin Wall", "1989"), ("the first Moon landing", "1969"), ("the start of the French Revolution", "1789"),
    ("the end of World War II in Europe", "1945"), ("the sinking of the Titanic", "1912"), ("the founding of the United Nations", "1945"),
    ("the Chernobyl disaster", "1986"), ("the introduction of the euro as a currency", "1999|2002"), ("the Declaration of Independence of the United States", "1776"),
    ("the start of World War I", "1914"), ("Columbus's first voyage to the Americas", "1492"), ("the Reformation begun by Martin Luther", "1517"),
    ("the Peace of Westphalia", "1648"), ("the founding of the German Empire", "1871"), ("the Russian October Revolution", "1917"),
    ("the Apollo 11 mission", "1969"), ("the first iPhone presentation", "2007"), ("the reunification of Germany", "1990"),
    ("the Battle of Waterloo", "1815"), ("the discovery of America's sea route to India by Vasco da Gama", "1498"),
    ("the Magna Carta", "1215"), ("the Treaty of Versailles", "1919"), ("the first Olympic Games of the modern era", "1896"),
    ("the launch of Sputnik 1", "1957"), ("the fall of Constantinople", "1453"),
]

GEOGRAPHY = [
    ("What is the longest river in Germany?", "Was ist der längste Fluss Deutschlands?", "Rhine|Rhein"),
    ("What is the largest ocean on Earth?", "Welcher ist der größte Ozean der Erde?", "Pacific|Pazifik"),
    ("What is the highest mountain on Earth above sea level?", "Welcher ist der höchste Berg der Erde über dem Meeresspiegel?", "Everest"),
    ("Which planet is closest to the sun?", "Welcher Planet ist der Sonne am nächsten?", "Mercury|Merkur"),
    ("Which planet has the most prominent ring system?", "Welcher Planet hat das auffälligste Ringsystem?", "Saturn"),
    ("What is the largest planet in the solar system?", "Welcher ist der größte Planet des Sonnensystems?", "Jupiter"),
    ("What is the largest desert in the world that is not polar?", "Welches ist die größte nicht-polare Wüste der Welt?", "Sahara"),
    ("Which sea lies between Europe and Africa?", "Welches Meer liegt zwischen Europa und Afrika?", "Mediterranean|Mittelmeer"),
    ("What is the longest river in Africa?", "Welcher ist der längste Fluss Afrikas?", "Nile|Nil"),
    ("What is the smallest country in the world by area?", "Welches ist das flächenmäßig kleinste Land der Welt?", "Vatican|Vatikan"),
    ("Which is the largest country by area?", "Welches ist das flächenmäßig größte Land?", "Russia|Russland"),
    ("What is the deepest lake in the world?", "Welches ist der tiefste See der Welt?", "Baikal"),
    ("Which continent is the Sahara in?", "Auf welchem Kontinent liegt die Sahara?", "Africa|Afrika"),
    ("What is the currency of Japan?", "Wie heißt die Währung Japans?", "Yen"),
    ("What is the currency of Switzerland?", "Wie heißt die Währung der Schweiz?", "Franc|Franken"),
    ("What is the currency of the United Kingdom?", "Wie heißt die Währung des Vereinigten Königreichs?", "Pound|Pfund"),
    ("What is the square root of 144?", "Was ist die Quadratwurzel aus 144?", "12|zwölf"),
    ("How many continents are there on Earth by the common convention of seven?", "Wie viele Kontinente gibt es nach der üblichen Zählung von sieben?", "7|seven|sieben"),
    ("What gas do plants absorb for photosynthesis?", "Welches Gas nehmen Pflanzen für die Photosynthese auf?", "carbon dioxide|CO2|Kohlendioxid"),
    ("What is the chemical formula of table salt?", "Wie lautet die chemische Formel von Kochsalz?", "NaCl"),
]


def public_facts() -> list:
    """All public facts as (key, question_en, question_de, accepted answers separated by |)."""
    out = []
    for country, capital in CAPITALS:
        out.append((f"cap:{country}", f"What is the capital of {country}?", f"Was ist die Hauptstadt von {country}?", capital))
    for name, symbol in ELEMENTS:
        out.append((f"sym:{name}", f"What is the chemical symbol of {name}?", f"Wie lautet das chemische Symbol von {name}?", symbol))
    for name, number in ATOMIC_NUMBERS:
        out.append((f"num:{name}", f"What is the atomic number of {name}?", f"Wie lautet die Ordnungszahl von {name}?", number))
    for work, author in AUTHORS:
        out.append((f"aut:{work}", f"Who wrote '{work}'?", f"Wer schrieb '{work}'?", author))
    for thing, who in INVENTIONS:
        out.append((f"inv:{thing}", f"Who is associated with {thing}?", f"Mit wem wird {thing} verbunden?", who))
    for event, year in EVENTS:
        out.append((f"evt:{event}", f"In which year did {event} take place?", f"In welchem Jahr fand {event} statt?", year))
    for i, (qe, qd, ans) in enumerate(GEOGRAPHY):
        out.append((f"geo:{i}", qe, qd, ans))
    return out
