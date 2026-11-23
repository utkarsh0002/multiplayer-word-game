"""Editable English starter spellings for Word Rally's five categories."""


def _word_set(entries: str) -> frozenset[str]:
    words = entries.replace("\n", ",").split(",")
    return frozenset(word.strip().casefold() for word in words if word.strip())


# Separate entries with commas or line breaks. List accepted spelling variants explicitly.
WORD_LISTS = {
    "animals": _word_set("""
        aardvark, albatross, alligator, alpaca, anaconda, ant, anteater, antelope, ape, armadillo
        baboon, badger, bat, bear, beaver, bee, beetle, bison, boar, bobcat, buffalo, butterfly
        camel, capybara, cat, caterpillar, cheetah, chicken, chimpanzee, cobra, cow, crab, crocodile, crow
        deer, dingo, dog, dolphin, donkey, dove, dragonfly, duck
        eagle, earwig, echidna, eel, egret, elephant, elk, emu
        falcon, ferret, finch, flamingo, fly, fox, frog
        gazelle, gecko, gerbil, giraffe, goat, goose, gorilla, grasshopper, guinea pig
        hamster, hare, hawk, hedgehog, heron, hippopotamus, hornet, horse, hummingbird, hyena
        ibis, iguana, impala
        jackal, jaguar, jay, jellyfish
        kangaroo, kingfisher, kiwi, koala, kudu
        ladybird, ladybug, lemur, leopard, lion, lizard, llama, lobster, lynx
        macaw, magpie, manatee, meerkat, mink, mole, mongoose, monkey, moose, mosquito, moth, mouse, mule
        narwhal, newt, nightingale
        octopus, okapi, opossum, orangutan, orca, ostrich, otter, owl, ox, oyster
        panda, panther, parrot, peacock, pelican, penguin, pheasant, pig, pigeon, platypus, polar bear, porcupine, puffin, puma, python
        quail, quokka
        rabbit, raccoon, rat, rattlesnake, raven, reindeer, rhinoceros, robin
        salamander, salmon, sardine, scorpion, seahorse, seal, shark, sheep, shrimp, skunk, sloth, snail, snake, sparrow, spider, squid, squirrel, starfish, swan
        tapir, tiger, toad, tortoise, toucan, trout, tuna, turkey, turtle
        umbrellabird, urial
        vicuna, viper, vole, vulture
        walrus, warthog, wasp, weasel, whale, wolf, wombat, woodpecker
        x-ray tetra, xerus
        yak, yellowhammer
        zebra, zebu
    """),
    "countries": _word_set("""
        afghanistan, albania, algeria, andorra, angola, antigua and barbuda, argentina, armenia, australia, austria, azerbaijan
        bahamas, bahrain, bangladesh, barbados, belarus, belgium, belize, benin, bhutan, bolivia, bosnia and herzegovina, botswana, brazil, brunei, bulgaria, burkina faso, burundi
        cabo verde, cambodia, cameroon, canada, cape verde, central african republic, chad, chile, china, colombia, comoros, costa rica, cote d'ivoire, côte d'ivoire, croatia, cuba, cyprus, czech republic, czechia
        democratic republic of the congo, denmark, djibouti, dominica, dominican republic
        east timor, ecuador, egypt, el salvador, equatorial guinea, eritrea, estonia, eswatini, ethiopia
        fiji, finland, france
        gabon, gambia, georgia, germany, ghana, greece, grenada, guatemala, guinea, guinea-bissau, guyana
        haiti, honduras, hungary
        iceland, india, indonesia, iran, iraq, ireland, israel, italy, ivory coast
        jamaica, japan, jordan
        kazakhstan, kenya, kiribati, kuwait, kyrgyzstan
        laos, latvia, lebanon, lesotho, liberia, libya, liechtenstein, lithuania, luxembourg
        madagascar, malawi, malaysia, maldives, mali, malta, marshall islands, mauritania, mauritius, mexico, micronesia, moldova, monaco, mongolia, montenegro, morocco, mozambique, myanmar
        namibia, nauru, nepal, netherlands, new zealand, nicaragua, niger, nigeria, north korea, north macedonia, norway
        oman
        pakistan, palau, panama, papua new guinea, paraguay, peru, philippines, poland, portugal
        qatar
        republic of the congo, romania, russia, rwanda
        saint kitts and nevis, saint lucia, saint vincent and the grenadines, samoa, san marino, sao tome and principe, saudi arabia, senegal, serbia, seychelles, sierra leone, singapore, slovakia, slovenia, solomon islands, somalia, south africa, south korea, south sudan, spain, sri lanka, sudan, suriname, sweden, switzerland, syria
        tajikistan, tanzania, thailand, timor-leste, togo, tonga, trinidad and tobago, tunisia, turkey, turkmenistan, tuvalu, türkiye
        uae, uganda, uk, ukraine, united arab emirates, united kingdom, united states, united states of america, uruguay, usa, uzbekistan
        vanuatu, vatican city, venezuela, vietnam
        yemen
        zambia, zimbabwe
    """),
    "food": _word_set("""
        almond, anchovy, apple, apricot, artichoke, asparagus, avocado
        bacon, bagel, banana, basil, beans, beef, beetroot, biscuit, blackberry, blueberry, bread, broccoli, brownie, burrito, butter
        cabbage, cake, carrot, cashew, cauliflower, celery, cereal, cheese, cherry, chicken, chickpea, chili, chocolate, coconut, cookie, corn, croissant, cucumber, curry
        dal, date, doughnut, dragon fruit, dumpling, durian
        edamame, egg, eggplant, elderberry, enchilada
        falafel, feta, fig, fish, flatbread, flour, french fries, fried rice
        garlic, gelatin, ginger, granola, grape, grapefruit, gravy, guava
        halibut, hamburger, hazelnut, honey, hummus
        ice cream, iceberg lettuce, idli
        jalapeno, jalapeño, jam, jelly, jicama
        kale, kebab, ketchup, kimchi, kiwi, kohlrabi
        lamb, lasagna, lasagne, leek, lemon, lentil, lettuce, lime, lobster, lychee
        macaroni, mackerel, mango, maple syrup, marshmallow, meatball, melon, millet, miso, mushroom, mustard
        naan, nachos, nectarine, noodles, nutmeg
        oatmeal, octopus, okra, olive, omelet, omelette, onion, orange, oyster
        pancake, papaya, parsnip, pasta, pea, peach, peanut, pear, pecan, pepper, pesto, pickle, pineapple, pistachio, pizza, plum, pomegranate, popcorn, pork, potato, pretzel, pumpkin
        quesadilla, quiche, quince, quinoa
        radish, raisin, ramen, raspberry, ravioli, rhubarb, rice, risotto, rye
        salad, salmon, salsa, sandwich, sardine, sausage, seaweed, shrimp, soup, spaghetti, spinach, squash, steak, strawberry, sushi, sweet potato
        taco, tamale, tangerine, toast, tofu, tomato, tortilla, tuna, turkey, turnip
        ube, udon, ugli fruit
        vanilla, veal, venison, vinegar
        waffle, walnut, watermelon, wheat
        xiaolongbao
        yam, yeast, yogurt, yoghurt, yuzu
        ziti, zucchini
    """),
    "sports": _word_set("""
        alpine skiing, american football, archery, artistic gymnastics, athletics, australian rules football
        badminton, baseball, basketball, beach volleyball, biathlon, bobsleigh, bocce, bowling, boxing
        canoeing, climbing, cricket, cross country running, cross-country skiing, curling, cycling
        darts, decathlon, diving, dodgeball, dressage
        equestrian, esports
        fencing, field hockey, figure skating, football, freestyle skiing, futsal
        gaelic football, golf, gymnastics
        handball, high jump, hockey, horse racing, hurdles
        ice hockey, ice skating, inline skating
        javelin, judo, jujitsu
        karate, kayaking, kickboxing, kitesurfing
        lacrosse, long jump, luge
        marathon, mixed martial arts, motocross, mountain biking, muay thai
        netball, nordic combined
        obstacle racing, orienteering
        padel, parkour, pickleball, pole vault, polo, powerlifting
        quadball
        race walking, racquetball, rowing, rugby, running
        sailing, shot put, skateboarding, skating, skiing, snooker, snowboarding, soccer, softball, speed skating, squash, surfing, swimming
        table tennis, taekwondo, tennis, trail running, trampoline, triathlon
        ultimate, ultimate frisbee
        vaulting, volleyball
        water polo, water skiing, weightlifting, windsurfing, wrestling
        yachting
    """),
    "cities": _word_set("""
        abu dhabi, accra, addis ababa, alexandria, algiers, amman, amsterdam, ankara, antwerp, athens, atlanta, auckland
        baghdad, baku, baltimore, bangalore, bangkok, barcelona, beijing, beirut, belfast, bengaluru, berlin, bogotá, bogota, boston, brisbane, brussels, bucharest, budapest, buenos aires
        cairo, canberra, cape town, casablanca, chennai, chicago, colombo, copenhagen
        dakar, dallas, damascus, delhi, denver, detroit, dhaka, doha, dublin, dubai
        edinburgh, edmonton, eindhoven, el paso, essen
        florence, fort worth, frankfurt, fukuoka
        gdansk, geneva, genoa, glasgow, gothenburg, guangzhou
        hamburg, hanoi, havana, helsinki, ho chi minh city, hong kong, houston, hyderabad
        ibadan, indianapolis, innsbruck, islamabad, istanbul, izmir
        jakarta, jaipur, jerusalem, johannesburg
        kabul, kampala, karachi, kathmandu, khartoum, kigali, kingston, kinshasa, kolkata, kuala lumpur, kyiv, kyoto
        lagos, lahore, las vegas, lima, lisbon, london, los angeles, lyon
        madrid, manchester, manila, marrakesh, melbourne, mexico city, miami, milan, minneapolis, montreal, moscow, mumbai, munich
        nairobi, nanjing, naples, new delhi, new orleans, new york, new york city, nice, nicosia
        odessa, odesa, oklahoma city, omaha, osaka, oslo, ottawa, oxford
        palermo, paris, perth, philadelphia, phoenix, porto, prague, pune, pyongyang
        quebec city, quito
        rabat, recife, reykjavik, riga, rio de janeiro, riyadh, rome, rotterdam
        san diego, san francisco, san jose, san juan, santiago, sao paulo, são paulo, seattle, seoul, seville, shanghai, shenzhen, singapore, stockholm, sydney
        taipei, tallinn, tangier, tehran, tokyo, toronto, toulouse, tunis, turin
        udaipur, ulaanbaatar, utrecht
        valencia, valletta, vancouver, venice, vienna, vilnius
        warsaw, washington, wellington, winnipeg, wuhan
        xiamen, xi'an
        yangon, yaounde, yaoundé, yerevan, yokohama
        zagreb, zanzibar city, zhengzhou, zurich, zürich
    """),
}
