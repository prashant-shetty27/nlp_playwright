from playwright.sync_api import Page


class ResultPage:
    def __init__(self, page: Page):
        self.page = page

    def get_heading_popular(self):
        return self.page.get_by_role('heading', { 'name': 'Popular Hotels in Mumbai', 'level': 1 })

    def get_heading_nine_square(self):
        return self.page.get_by_role('heading', { 'name': 'Nine Square Residency', 'level': 3 })

    def get_textbox_start_date(self):
        return self.page.get_by_role('textbox', name='Start Date')

    def get_textbox_end_date(self):
        return self.page.get_by_role('textbox', name='End Date')

    def get_textbox_name(self):
        return self.page.get_by_role('textbox', name='Name')

    def get_textbox_mobile(self):
        return self.page.get_by_role('textbox', name='Mobile Number')

    def get_sort_by(self):
        return self.page.get_by_text('Sort by', exact=True)

    def get_star_rating(self):
        return self.page.get_by_text('Star Rating', exact=True)

    def get_budget(self):
        return self.page.get_by_text('Budget', exact=True)

    def get_hotel_view(self):
        return self.page.get_by_text('Hotel View', exact=True)

    def get_pets_essential(self):
        return self.page.get_by_text('Pets Essential', exact=True)

    def get_filter_prev_next(self):
        return self.page.locator('#filter_prev_next')

    def get_all_filters_btn(self):
        return self.page.get_by_text('All Filters', exact=True)

    def get_img_hotel_golden_suites(self):
        return self.page.get_by_role('img', name='Hotel Golden Suites')

    def get_img_slide_1(self):
        return self.page.get_by_role('img', name='Slide 1 Image of Nine Square Residency Near Vitthal Mandir, Mumbai')

    def get_price_display(self):
        return self.page.get_by_text('per night', exact=True)

    def get_ratings_info(self):
        return self.page.get_by_role('presentation', name='Ratings : 4.7')

    def get_ratings_count(self):
        return self.page.get_by_role('none', name='108 Ratings')

    def get_imgtag_icon(self):
        return self.page.locator('div.imgtag_icon.jdicon')

    def get_locat_icon(self):
        return self.page.locator('div.resultbox_locat_icon.jdicon')

    def get_location_text(self):
        return self.page.get_by_text('Lokmanya Tilak Road, Mumbai', exact=True)

    def get_wifi_ac_list(self):
        return self.page.get_by_role('list', name='WiFi\nAC')

    def get_whatsapp_container(self):
        return self.page.locator('div.jsx-da112b0f0664a117.resultbox_btn_wpr')

    def get_whatsapp_btn(self):
        return self.page.get_by_text('WhatsApp', exact=True)

    def get_best_deal_btn(self):
        return self.page.get_by_text('Get Best Deal', exact=True)

    def get_advance_deal_box(self):
        return self.page.locator('div.jsx-f02aee9c9095d0d0.advance_deal_box')

    def get_hotel_type_question(self):
        return self.page.get_by_text('What type of Hotel are you looking for?', exact=True)

    def get_luxury(self):
        return self.page.get_by_text('Luxury', exact=True)

    def get_others(self):
        return self.page.get_by_text('Others', exact=True)

    def get_animicon(self):
        return self.page.locator('span.jsx-40d940966fd61fec.animicon')

    def get_i_agree_to(self):
        return self.page.get_by_text('I Agree to', exact=True)

    def get_terms_link(self):
        return self.page.get_by_role('link', name='Terms Conditions Privacy Policy')

    def get_tnc_icon(self):
        return self.page.locator('div.jsx-3637087745.tnc-icon')

    def get_apply_button(self):
        return self.page.get_by_role('button', name='Apply')

