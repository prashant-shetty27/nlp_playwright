import logging
from playwright.sync_api import Page
from ml_engine import LocatorHealer

logger = logging.getLogger(__name__)

# Initialize the Machine Learning model once to save memory
ml_healer = LocatorHealer()

def scrape_current_dom(page: Page):
    """
    Injects JavaScript to rapidly harvest the DNA of every visible element on the page.
    This acts as the 'Candidate' dataset for our Machine Learning model.
    """
    logger.info("🔍 Scraping current DOM for ML candidates...")
    
    js_payload = """
    () => {
        // Grab every element on the page
        const elements = Array.from(document.querySelectorAll('*'));
        
        const candidates = elements.map(el => {
            const rect = el.getBoundingClientRect();
            
            // Architectural Optimization: Ignore elements with 0 width/height. 
            // If a user can't see it, they can't click it. This removes 70% of DOM noise.
            if (rect.width === 0 || rect.height === 0) return null;
            
            let attributesData = {};
            for (let i = 0; i < el.attributes.length; i++) {
                let attr = el.attributes[i];
                if (attr.value && attr.value.length < 300) {
                    attributesData[attr.name] = attr.value;
                }
            }
            
            return {
                tagName: el.tagName.toLowerCase(),
                className: el.className || null,
                innerText: el.innerText ? el.innerText.substring(0, 100).trim() : null,
                rect: {
                    x: Math.round(rect.x), 
                    y: Math.round(rect.y), 
                    width: Math.round(rect.width), 
                    height: Math.round(rect.height)
                },
                attributes: attributesData
            };
        });
        
        // Filter out the nulls (invisible elements) and return to Python
        return candidates.filter(e => e !== null);
    }
    """
    return page.evaluate(js_payload)

def ml_heal_element(page: Page, target_dna: dict):
    """
    The Self-Healing orchestration loop.
    1. Scrapes the broken page.
    2. Feeds data to Scikit-Learn.
    3. Builds a new custom XPath for the winner.
    """
    # 1. Get the current state of the broken page
    current_page_elements = scrape_current_dom(page)
    
    logger.info(f"🧠 ML Engine analyzing {len(current_page_elements)} candidate elements...")
    
    # 2. Ask the Scikit-Learn model to find the Nearest Neighbor
    winner_dna = ml_healer.train_and_predict(target_dna, current_page_elements)
    
    if not winner_dna:
        logger.error("❌ ML Engine could not confidently match an element.")
        return None
        
    # 3. We must convert the winning ML payload back into an XPath so Playwright can click it
    # We will reuse the same logic we built in the spy_server
    return generate_fallback_xpath(winner_dna)

def generate_fallback_xpath(element_dna):
    """
    Converts a winning ML payload into a usable Playwright XPath.
    Upgraded to prioritize semantic attributes over brittle classes.
    """
    tag = element_dna.get("tagName", "*")
    attrs = element_dna.get("attributes", {})
    
    # 1. Developer IDs
    if attrs.get("id"): return f"//{tag}[@id='{attrs['id']}']"
    if attrs.get("name"): return f"//{tag}[@name='{attrs['name']}']"
        
    # 2. Accessibility & Semantic Tags
    if attrs.get("aria-label"): return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"): return f"//{tag}[@title='{attrs['title']}']"
    if attrs.get("alt"): return f"//{tag}[@alt='{attrs['alt']}']"
        
    # 3. Class Logic
    classes = attrs.get("class", "")
    if classes:
        class_list = classes.split()
        valid_classes = [c for c in class_list if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join([f"contains(@class, '{c}')" for c in valid_classes])
            return f"//{tag}[{contains_logic}]"
            
    # 4. Text Logic
    text = element_dna.get("innerText")
    if text and len(text) < 40:
        clean_text = text.replace("'", "\\'")
        return f"//{tag}[normalize-space(text())='{clean_text}']"
        
    return f"//{tag}"